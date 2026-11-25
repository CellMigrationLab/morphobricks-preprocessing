"""Regression (owner's Colab test of the v0.9.0 candidate, 25 November 2026): a pick never saves.

On main at b683c7c every dropdown of the shipped notebook's channel step, and every image-type
radio button of a folder, wrote the draft at once (and redrew the page). These tests use only
what both versions have (the stepper, the widgets on screen), so they run, and fail, there too.
"""

from __future__ import annotations

import pytest

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.description_file import DRAFT_FILENAME

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]


def _tree(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, name in enumerate(FOLDERS):
        write_channels(root / name / "a.tif", None, ["blobs", "filaments", "cells"], seed=i + 1)
        write_masks(masks, {f"{name}/a.tif": i + 1}, {"Nuclei": "labels"})
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.welcome()
    nb.steps()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    return nb, tmp_path / "out" / DRAFT_FILENAME


def _all(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _all(child)


def _shown(widget):
    return getattr(widget.layout, "display", None) != "none"


def _bytes(path):
    return path.read_bytes() if path.exists() else None


def test_choosing_a_folder_s_image_type_writes_nothing(tmp_path):
    nb, draft = _tree(tmp_path)
    nb.type_line.per_folder.selected_index = 0  # Set per folder…
    before = _bytes(draft)
    radio = next(iter(nb.channel_types.radios.values()))
    radio.value = "channels"
    assert _bytes(draft) == before


def test_picking_a_channel_writes_nothing(tmp_path):
    nb, draft = _tree(tmp_path)
    nb.type_line.apply.click()  # the image type for all folders (a deliberate save)
    card = nb.channel_labels
    for row, text in zip(card.rows, ["DNA", "ACTIN", "PHASE"], strict=False):
        row.id_box.value = text
    card.confirm.button.click()
    for toggle in _all(nb.channel_grid.widget):
        if isinstance(toggle, widgets.ToggleButtons) and toggle.options[0][1] == "yes":
            toggle.value = "yes"  # the guided step's question, where there is one
    before = _bytes(draft)
    drops = [w for w in _all(nb.channel_grid.widget) if isinstance(w, widgets.Dropdown)]
    drop = next(d for d in drops if _shown(d))
    drop.value = "DNA"
    assert _bytes(draft) == before
