"""The *Your masks* screen: one question per mask type, no default (Stage 1, PR 16).

Widgets are driven headless, as in ``test_notebook_channels``. The screen opens only when the
folder check paired masks and the axis roles are confirmed; each answer is saved at once in
``morphobricks.draft.json``; the mask ID is the folder name when it is a valid mask ID.
"""

import numpy as np
import pytest
import tifffile

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from morphobricks_preprocessing.draft_format import answers_from_bytes

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
KINDS = ["blobs", "filaments", "cells"]


def experiment(tmp_path, kinds=None):
    """Four units with one 3-channel image each, and a masks folder of the given mask types."""
    root = tmp_path / "exp"
    seeds = {}
    for i, folder in enumerate(FOLDERS):
        write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=i + 1)
        seeds[f"{folder}/a.tif"] = i + 1
    kinds = {"Nuclei": "labels", "tissue": "region"} if kinds is None else kinds
    return root, write_masks(tmp_path / "masks", seeds, kinds)


def open_masks(root, masks, out, *, confirm_axes=True):
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.channels_step, nb.masks_step):
        step()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    if confirm_axes:
        nb.preview_checked.set(True)
        nb.show_preview()
        nb.confirm_button.click()
        assert nb.confirmed_mapping is not None
    return nb


def saved(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes())


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def test_one_question_per_mask_type_with_nothing_chosen(tmp_path, out):
    nb = open_masks(*experiment(tmp_path), out)
    card = nb.mask_card
    assert ui.is_visible(card.widget)
    assert sorted(card.questions) == ["Nuclei", "tissue"]
    for question in card.questions.values():
        assert question.choice.value is None  # no default (v1 4.5)
        assert all(box.disabled for box in question.made_from.values())
    assert not card.complete
    assert "Still to answer: 2 mask types" in card.status.value
    assert not (out / DRAFT_FILENAME).exists()  # nothing saved before an answer


def test_the_evidence_is_shown_beside_the_question(tmp_path, out):
    nb = open_masks(*experiment(tmp_path), out)
    nuclei = nb.mask_card.questions["Nuclei"]
    facts = nuclei.facts.value
    assert "4 of 4 images have one" in facts
    assert "uint16" in facts
    assert "8 different values in ctrl/rep1/a.tif" in facts  # 0 and seven objects
    assert "the mask alone in grey" in facts
    assert len(nuclei.pictures.children) == 2  # the image channel and the mask
    tissue = nb.mask_card.questions["tissue"].facts.value
    assert "uint8" in tissue and "2 different values" in tissue


def test_the_mask_id_is_the_folder_name_when_valid(tmp_path, out):
    nb = open_masks(*experiment(tmp_path), out)
    nuclei = nb.mask_card.questions["Nuclei"]
    assert nuclei.id_box.value == "Nuclei"
    assert "the folder name" in nuclei.id_state.value
    nuclei.choice.value = "instances"
    answers = saved(out)
    assert set(answers.masks) == {"Nuclei"}
    mask = answers.masks["Nuclei"]
    assert (mask.source_type, mask.representation) == ("Nuclei", "instances")
    assert "each object outlined" in nuclei.facts.value  # drawn as declared


def test_a_folder_name_that_cannot_be_an_id_gets_no_proposal(tmp_path, out):
    root, masks = experiment(tmp_path, {"nuclei-mask": "labels", "cell": "labels"})
    nb = open_masks(root, masks, out)
    dashed, reserved = nb.mask_card.questions["nuclei-mask"], nb.mask_card.questions["cell"]
    assert dashed.id_box.value == "" and reserved.id_box.value == ""
    dashed.choice.value = "instances"  # refused: no ID yet
    assert "Not taken" in dashed.message.value
    assert dashed.choice.value is None
    assert not (out / DRAFT_FILENAME).exists()
    dashed.id_box.value = "nuclei_mask"
    dashed.choice.value = "instances"
    assert set(saved(out).masks) == {"nuclei_mask"}
    reserved.id_box.value = "cell"
    assert "E_ID_RESERVED" in reserved.id_state.value


def test_regions_are_saved_as_binary_and_a_typed_id_follows(tmp_path, out):
    nb = open_masks(*experiment(tmp_path), out)
    tissue = nb.mask_card.questions["tissue"]
    tissue.choice.value = "binary"
    assert saved(out).masks["tissue"].representation == "binary"
    tissue.id_box.value = "Tissue_area"
    answers = saved(out)
    assert set(answers.masks) == {"Tissue_area"}
    assert answers.masks["Tissue_area"].source_type == "tissue"
    assert "the inside tinted" in tissue.facts.value


def test_made_from_offers_the_declared_labels_only_after_an_answer(tmp_path, out):
    nb = open_masks(*experiment(tmp_path), out)
    labels = nb.channel_labels
    for row, text in zip(labels.rows, ["DNA", "ACTIN", "PHASE"], strict=True):
        row.id_box.value = text
    labels.confirm.button.click()
    nuclei = nb.mask_card.questions["Nuclei"]
    assert sorted(nuclei.made_from) == ["ACTIN", "DNA", "PHASE"]
    assert all(box.disabled for box in nuclei.made_from.values())
    nuclei.choice.value = "instances"
    nuclei = nb.mask_card.questions["Nuclei"]
    nuclei.made_from["DNA"].value = True
    assert saved(out).masks["Nuclei"].source_channels == ("DNA",)


def test_every_answer_completes_the_card_and_resumes(tmp_path, out):
    root, masks = experiment(tmp_path)
    nb = open_masks(root, masks, out)
    nb.mask_card.questions["Nuclei"].choice.value = "instances"
    nb.mask_card.questions["tissue"].choice.value = "binary"
    assert nb.mask_card.complete
    assert "Every mask type is answered" in nb.mask_card.status.value
    again = open_masks(root, masks, out)
    assert again.mask_card.questions["Nuclei"].choice.value == "instances"
    assert again.mask_card.questions["tissue"].choice.value == "binary"


def test_the_screen_waits_for_the_axis_roles_and_hides_without_masks(tmp_path, out):
    root, masks = experiment(tmp_path)
    nb = open_masks(root, masks, out, confirm_axes=False)
    assert not ui.is_visible(nb.mask_card.widget)
    assert "Waiting for the axis roles" in nb.masks_waiting.value
    plain = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "o2"))
    for step in (plain.folder_step, plain.axis_step, plain.masks_step):
        step()
    plain.check_button.click()
    assert not ui.is_visible(plain.mask_card.widget)
    assert "No masks" in plain.masks_waiting.value


def test_a_declared_mask_whose_files_are_gone_can_be_removed(tmp_path, out):
    root, masks = experiment(tmp_path)
    nb = open_masks(root, masks, out)
    nb.mask_card.questions["tissue"].choice.value = "binary"
    for path in sorted((masks / "tissue").rglob("*.tif")):
        path.unlink()
    again = open_masks(root, masks, out)
    assert sorted(again.mask_card.questions) == ["Nuclei"]
    gone = again.mask_card.gone.children
    assert len(gone) == 1 and "tissue" in gone[0].children[0].value
    gone[0].children[1].click()
    assert saved(out).masks == {}


def write_grouped(masks, root):
    """Write a ``mito`` mask per image: two values, each in three separate pieces."""
    for image in sorted(root.rglob("*.tif")):
        labels = np.zeros((64, 64), np.uint16)
        for value, row in ((1, 4), (2, 40)):
            for column in (4, 24, 44):
                labels[row : row + 6, column : column + 6] = value
        path = masks / "mito" / image.relative_to(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(path, labels, metadata={"axes": "YX"}, photometric="minisblack")


def test_grouped_labels_are_offered_in_plain_words_and_never_preselected(tmp_path, out):
    # Spec experiment-v1 4.5b (PL1): the scan's `grouped` kind is evidence; the user picks.
    from morphobricks_preprocessing import preflight, scan_masks
    from morphobricks_preprocessing.notebook_mask_question import OPTIONS

    root, masks = experiment(tmp_path)
    write_grouped(masks, root)
    report = scan_masks(preflight(root, masks_folder=masks).masks, root)
    assert report.types["mito"].kind == "grouped"
    nb = open_masks(root, masks, out)
    mito = nb.mask_card.questions["mito"]
    assert mito.choice.value is None  # never pre-filled from the scan
    words = "grouped labels (one value per parent, e.g. mitochondria by cell)"
    assert (words, "parent_labels") in OPTIONS and words in mito.choice.options[2]
    assert mito.widget.layout.overflow is None  # one column, no inner scroll box
    reason = mito.made_box.children[-1].value  # a disabled action says why
    assert "answer the question first" in reason or "declare them on Channels" in reason
    mito.choice.value = "parent_labels"
    assert saved(out).masks["mito"].representation == "parent_labels"
    assert "each group outlined in its own colour" in mito.facts.value
    assert nb._mask_kinds()["mito"] == "grouped labels"
