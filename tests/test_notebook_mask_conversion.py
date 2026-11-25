"""The notebook's **Convert a mask** card (Spec ADR 0005 BM12): it calls the conversion helpers.

Built headless and driven by ``.value`` and ``button.click()``: the sources offered follow the
operation, the button waits for a source and a name, a click writes the new mask files with their
lineage lines, the same click again writes nothing, and a refusal is a red card.
"""

import importlib
import json

import pytest

from conversion_tree import CLASSES, lines, run_with, tree

pytest.importorskip("ipywidgets")
cards = importlib.import_module("morphobricks_preprocessing.notebook_mask_conversion")


def test_the_card_offers_the_declared_masks_each_helper_converts(tmp_path):
    run, _, _ = run_with(tmp_path, extra={"Classes": CLASSES})
    card = cards.MaskConversionCard()
    assert card.button.disabled and "Available after a run" in card.hint.value
    card.refresh(run.output_folder)
    assert card.hint.value == ""
    assert list(card.source.options) == ["TumorRegion"]
    card.operation.value = "classes_to_binary"
    assert list(card.source.options) == ["Classes", "Nuclei"]
    assert "_class" in card.name_help.value
    assert card.button.disabled  # no name yet
    card.name.value = "Tissue"
    assert not card.button.disabled


def test_every_disabled_button_shows_what_blocks_it(tmp_path):
    # AGENTS.md "Disabled actions": a visible blocked line beside every disabled button.
    card = cards.MaskConversionCard()
    children = list(card.card.children)
    assert children.index(card.blocker) == children.index(card.button) + 1  # beside it
    assert card.button.disabled
    assert "no run in the output folder declares masks" in card.blocker.value
    run, _, _ = run_with(tmp_path)  # v08: TumorRegion binary, Nuclei instances
    card.refresh(run.output_folder)
    assert card.button.disabled and "type the new mask ID" in card.blocker.value
    card.name.value = "TumorObjects"
    assert not card.button.disabled and card.blocker.value == ""
    card.operation.value = "classes_to_binary"
    card.name.value = ""
    assert card.button.disabled and "type the prefix of the new masks" in card.blocker.value
    card.name.value = "Tissue"
    assert not card.button.disabled and card.blocker.value == ""
    card._declared = {"TumorRegion": "binary"}  # no mask this helper converts
    card._operation_changed()
    assert card.button.disabled
    assert "declares no parent_labels or instances mask" in card.blocker.value


def test_a_click_converts_every_image_and_names_the_next_steps(tmp_path):
    run, image, masks = run_with(tmp_path)
    card = cards.MaskConversionCard()
    card.refresh(run.output_folder)
    card.name.value = "TumorObjects"
    card.button.click()
    assert "The mask is converted." in card.status.value
    assert "declare the mask TumorObjects (source type TumorObjects) as instances" in (
        card.status.value
    )
    assert f"TumorObjects/{image}" in card.result.value
    assert json.loads(lines(masks, "TumorObjects"))["operation"] == "binary_to_instances"
    before = tree(masks)
    card.button.click()  # the same conversion again: nothing is written
    assert tree(masks) == before
    assert "already written before: 1" in card.status.value


def test_a_refusal_is_a_red_card_and_writes_nothing(tmp_path):
    run, image, masks = run_with(tmp_path, extra={"Classes": CLASSES})
    card = cards.MaskConversionCard()
    card.refresh(run.output_folder)
    card.operation.value = "classes_to_binary"
    card.source.value = "Classes"
    card.name.value = "in"
    before = tree(masks)
    assert card.convert() is None
    assert "The mask was not converted." in card.status.value
    assert "breaks the mask ID rules" in card.status.value
    assert tree(masks) == before


def test_the_notebook_shows_the_card_under_describe_the_masks():
    notebook = importlib.import_module("morphobricks_preprocessing.notebook_ui")
    pytest.importorskip("matplotlib")
    nb = notebook.PreprocessingNotebook(".")
    assert isinstance(nb.mask_conversion, cards.MaskConversionCard)
    page = nb.run_step()
    assert nb.mask_conversion.card in page.children[1].children


def test_a_declared_mask_without_recorded_files_is_refused_not_converted(tmp_path):
    """#481: a source the run recorded no file of is a red card, never a green one."""
    run, _, masks = run_with(tmp_path, declared=(("Unfiled", "unfiled", "binary"),))
    card = cards.MaskConversionCard()
    card.refresh(run.output_folder)
    card.source.value = "Unfiled"
    card.name.value = "UnfiledObjects"
    before = tree(masks)
    card.button.click()
    assert "The mask was not converted." in card.status.value
    assert "The run recorded no Unfiled mask file (source type unfiled)" in card.status.value
    assert "converted." not in card.status.value.replace("not converted.", "")
    assert card.result.value == "" and tree(masks) == before  # no output, no lineage line
    assert not card.button.disabled  # the click reads the manifest again: retry after the run
