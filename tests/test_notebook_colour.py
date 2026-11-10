"""Colour (RGB) images are shown as one colour picture (owner decision, PR 17b).

The renderer (:func:`.preview_render.colour_composite`) is pure and display only: channel 0 red,
1 green, 2 blue, each with its own contrast; the pictures it makes are checked pixel by pixel.
The notebook uses it wherever a colour folder's picture is drawn: the tag grid, the image-type
page, the pairing and *Check and save*.
"""

import numpy as np
import pytest

from channel_images import write_rgb
from morphobricks_preprocessing.preview_render import (
    COMPOSITE,
    colour_composite,
    display_panel,
    png_bytes,
)
from test_notebook_check_save import decode

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]


def _panels():
    ramp = np.arange(64, dtype=np.uint16).reshape(8, 8)
    return [display_panel(ramp), display_panel(ramp.T.copy()), display_panel(63 - ramp)]


def test_the_composite_puts_each_channel_in_its_colour_with_its_own_contrast():
    panels = _panels()
    composite = colour_composite(panels)
    assert composite.kind == COMPOSITE and composite.pixels.shape == (8, 8, 3)
    for c in range(3):
        assert np.array_equal(composite.pixels[..., c], panels[c].pixels)
    shown = decode(png_bytes(composite.pixels))
    assert shown.shape == (8, 8, 3) and np.array_equal(shown, composite.pixels)
    assert (shown[..., 0] != shown[..., 1]).any()  # really colour, not grey


def test_the_composite_never_changes_the_planes_it_is_given():
    planes = [np.full((4, 4), 7, np.uint16), np.arange(16, dtype=np.uint16).reshape(4, 4)]
    planes.append(planes[1].copy())
    copies = [p.copy() for p in planes]
    composite = colour_composite([display_panel(p) for p in planes])
    assert all(np.array_equal(a, b) for a, b in zip(planes, copies, strict=True))
    assert composite.note == "flat: red" and (composite.pixels[..., 0] == 128).all()


@pytest.mark.parametrize(
    "panels",
    [
        lambda p: p[:2],
        lambda p: [*p[:2], display_panel(np.array([[np.nan, 1.0], [2.0, 3.0]]))],
        lambda p: [*p[:2], display_panel(np.zeros((4, 4), np.uint16))],
    ],
)
def test_the_composite_refuses_what_is_not_three_finite_planes_of_one_shape(panels):
    with pytest.raises(ValueError):
        colour_composite(panels(_panels()))


# --- in the notebook ---------------------------------------------------------------------------

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402


def _images(widget):
    if widget.__class__.__name__ == "Image" and widget.value:
        yield widget
    for child in getattr(widget, "children", ()):
        yield from _images(child)


@pytest.fixture
def rgb_nb(tmp_path):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        write_rgb(root / folder / "a.tif", seed=i + 1)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    card = nb.channel_labels
    card.rows[0].id_box.value = "DNA"
    card.confirm.button.click()
    return nb


def _colour(png):
    pixels = decode(bytes(png))
    return pixels.shape[2] == 3 and bool((pixels[..., 0] != pixels[..., 2]).any())


def test_every_colour_folder_is_one_colour_picture_on_tags_and_image_type(rgb_nb):
    grid = [w for w in _images(rgb_nb.channel_grid.blocks)]
    assert len(grid) == len(FOLDERS)  # one picture per folder, not three grey planes
    assert all("mb-colour" in w._dom_classes and _colour(w.value) for w in grid)
    types = [w for w in _images(rgb_nb.type_rows.widget)]
    assert len(types) == len(FOLDERS) and all(_colour(w.value) for w in types)
    assert "channel 1 red, 2 green, 3 blue" in repr(rgb_nb.type_rows.rows.children)


def test_the_enlarged_colour_picture_is_the_1024_px_view(rgb_nb):
    grid = rgb_nb.channel_grid
    zoom = next(w for w in _all(grid.blocks) if getattr(w, "description", "").strip() == "Enlarge")
    zoom.click()
    assert "colour picture" in grid.detail.children[0].value
    assert _colour(grid.detail.children[1].value)


def _all(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _all(child)


def test_pairing_and_check_and_save_draw_colour_folders_in_colour(rgb_nb):
    pictures = [w for w in _images(rgb_nb.replicates_box)] + [
        w for w in _images(rgb_nb.design_card)
    ]
    assert pictures and all(_colour(w.value) for w in pictures)
