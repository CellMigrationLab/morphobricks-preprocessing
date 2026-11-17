"""The notebook's **Make an H&E tissue mask** card (Spec ADR 0005 BM15; design §5, §7).

Built headless and driven by ``.value`` and ``button.click()``: every disabled action names its
reason, the preview computes in memory and writes nothing, and the write happens only on the
click, showing the files written and the next steps.
"""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from he_tissue_tree import IDENTITY_PATCH, UNIT, channels_run, identities, rgb_run, tree

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
cards = importlib.import_module("morphobricks_preprocessing.notebook_tissue_mask")
IMAGE = f"{UNIT}/a.tif"


def test_the_card_names_every_reason_its_button_is_disabled(tmp_path, monkeypatch):
    card = cards.TissueMaskCard()
    children = list(card.card.children)
    assert children.index(card.blocker) == children.index(card.button) + 1  # beside it
    assert card.button.disabled and "no run in the output folder" in card.blocker.value
    assert "Available after a run with a unit declared rgb" in card.hint.value
    card.refresh(channels_run(tmp_path / "channels").output_folder)
    assert not card.has_rgb and "the run has no unit declared rgb" in card.blocker.value
    run = rgb_run(tmp_path / "rgb")
    card.refresh(run.output_folder)
    assert card.has_rgb and card.hint.value == ""
    assert list(card.unit.options) == [UNIT] and list(card.positions.options) == [IMAGE]
    assert "choose at least one position" in card.blocker.value
    assert "type the new mask ID" in card.blocker.value
    card.add_unit.click()  # all of this unit
    assert card.positions.value == (IMAGE,)
    for name, reason in (
        ("Nuclei", "is declared already"),
        ("NUCLEI", "differs only in case"),
        ("1x", "breaks the mask ID rules"),
    ):
        card.name.value = name
        assert card.button.disabled and reason in card.blocker.value
    card.name.value = "HETissue"
    assert not card.button.disabled and card.blocker.value == ""
    monkeypatch.setattr(cards.importlib.util, "find_spec", lambda name: None)
    card._update_button()
    assert "pip install &quot;morphobricks-preprocessing[he-mask]&quot;" in card.blocker.value


def test_a_run_without_a_masks_folder_is_named(tmp_path):
    card = cards.TissueMaskCard()
    card.refresh(rgb_run(tmp_path, masks=False).output_folder)
    assert "the run has no masks folder" in card.blocker.value


def test_positions_that_cannot_be_used_are_listed_greyed_with_their_reason(tmp_path, monkeypatch):
    run = rgb_run(tmp_path)
    (own,) = run.fovs()
    stack = dataclasses.replace(own, relative_path=f"{UNIT}/z.tif", z_count=3)
    monkeypatch.setattr(cards.RunInputs, "fovs", lambda self: (own, stack))
    card = cards.TissueMaskCard()
    card.refresh(run.output_folder)
    assert list(card.positions.options) == [IMAGE]
    assert f"{UNIT}/z.tif: a Z or T stack (3 Z planes, 1 time points)" in card.unusable.value


def test_the_preview_writes_nothing_and_shows_the_shares_and_notes(tmp_path):
    pytest.importorskip("skimage")
    run = rgb_run(tmp_path)
    card = cards.TissueMaskCard()
    card.refresh(run.output_folder)
    before = tree(run.masks_root)
    card.preview_button.click()
    assert tree(run.masks_root) == before
    assert (
        bytes(card.picture.value).startswith(b"\x89PNG") and card.picture.layout.display != "none"
    )
    text = card.numbers.value
    assert "kept 80.4 % · nuclei 1.6 % · red cells 0.8 % · empty 17.2 %" in text
    assert "Sizes are in pixels. Fixed rule he_tissue_v1 (provisional)" in text
    assert "this mask will not be reused on retry" in text  # an editable checkout


def test_the_write_happens_only_on_the_click(tmp_path, monkeypatch):
    pytest.importorskip("skimage")
    monkeypatch.setattr(IDENTITY_PATCH, lambda: identities())
    run = rgb_run(tmp_path)
    card = cards.TissueMaskCard()
    card.refresh(run.output_folder)
    before = tree(run.masks_root)
    card.positions.value = (IMAGE,)
    card.name.value = "HETissue"
    assert tree(run.masks_root) == before  # choosing writes nothing
    card.button.click()
    assert f"HETissue/{IMAGE}" in tree(run.masks_root)
    assert "Written: 1 file(s); already written before: 0." in card.status.value
    assert "declare the mask HETissue (source type HETissue) as binary" in card.status.value
    assert f"HETissue/{IMAGE}: kept 80.4 %" in card.result.value
    card.button.click()  # the same click again: nothing changes
    assert "Written: 0 file(s); already written before: 1." in card.status.value


def test_a_refusal_during_the_write_is_a_red_card_naming_the_position(tmp_path, monkeypatch):
    pytest.importorskip("skimage")
    run = rgb_run(tmp_path)
    card = cards.TissueMaskCard()
    card.refresh(run.output_folder)
    card.positions.value = (IMAGE,)
    card.name.value = "HETissue"
    card.button.click()  # unverified here: written, never reused
    card.button.click()
    assert f"No tissue mask was written for {IMAGE}." in card.status.value
    assert "never reused" in card.status.value


def test_the_card_sits_beside_convert_a_mask_and_shows_only_for_an_rgb_run(tmp_path):
    from morphobricks_preprocessing import notebook_style as ui
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    run = rgb_run(tmp_path)
    nb = PreprocessingNotebook(str(run.experiment_root))
    nb.welcome()
    nb.folder_step()
    page = nb.run_step()
    children = list(page.children[1].children)
    assert children.index(nb.tissue_mask.card) == children.index(nb.mask_conversion.card) + 1
    nb.output.value = str(tmp_path / "elsewhere")
    nb._refresh_mask_step()
    assert not ui.is_visible(nb.tissue_mask.card)
    nb.output.value = str(run.output_folder)
    nb._refresh_mask_step()
    assert nb.tissue_mask.has_rgb and ui.is_visible(nb.tissue_mask.card)


def test_a_mask_id_with_spaces_keeps_the_button_disabled_and_writes_nothing(tmp_path):
    # The reasons checked the trimmed ID, the write used the typed one: the button was enabled
    # for an ID the helper then refused (review of #323). The typed ID is never trimmed.
    run = rgb_run(tmp_path)
    before = tree(run.masks_root)
    card = cards.TissueMaskCard()
    card.refresh(run.output_folder)
    card.add_unit.click()
    card.name.value = " HETissue "
    assert card.button.disabled and "breaks the mask ID rules" in card.blocker.value
    assert card.write() is None
    assert tree(run.masks_root) == before
    card.name.value = "   "
    assert card.button.disabled and "type the new mask ID" in card.blocker.value
