"""Folder pictures with a native crop on *Images found* and *Replicates* (owner decisions, 17c).

*Images found* draws each folder's first image from its header only when the header names Y and
X, max-projecting and naming every other axis, within a cap on planes and pixels; a large
thumbnail gets a crop at native pixels where the picture varies most (deterministic, topmost then
leftmost). All of it is display only: the source arrays and files are never changed.
"""

import numpy as np
import pytest
import tifffile

from channel_images import blobs, large, write_channels, write_rgb
from morphobricks_preprocessing import header_preview as hp
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.metadata import inspect_image
from morphobricks_preprocessing.preview_crop import CROP_SIDE, busiest_square, tile_pictures
from morphobricks_preprocessing.preview_render import display_panel
from test_notebook_check_save import decode

# --- the crop (pure) ------------------------------------------------------------------------------


def _busy_at(y0, x0, size=1024):
    plane = np.full((size, size), 100, np.int64)
    rng = np.random.default_rng(0)
    plane[y0 : y0 + CROP_SIDE, x0 : x0 + CROP_SIDE] += rng.integers(0, 4000, (CROP_SIDE, CROP_SIDE))
    return plane.astype(np.uint16)


def test_the_crop_is_the_busiest_square_at_native_pixels_with_the_thumbnail_contrast():
    plane = _busy_at(256, 640)
    before = plane.copy()
    found = tile_pictures([plane], False, 256)
    assert np.array_equal(plane, before)  # the source array is never changed
    assert found.factor == 4 and found.crop_box == (256, 384, 640, 768)
    assert "x 640-767, y 256-383" in found.crop_text
    crop = decode(found.crop_png)[..., 0]  # grey
    assert crop.shape == (2 * CROP_SIDE, 2 * CROP_SIDE)
    native = crop[::2, ::2]
    assert np.array_equal(crop, np.repeat(np.repeat(native, 2, axis=0), 2, axis=1))  # nearest
    thumb = display_panel(plane[::4, ::4])
    expected = display_panel(plane[256:384, 640:768], (thumb.low, thumb.high)).pixels
    assert np.array_equal(native, expected)  # every native pixel, the thumbnail's contrast
    assert decode(found.png).shape == (256, 256, 1)


def test_a_tie_goes_to_the_topmost_then_leftmost_square_and_is_the_same_every_time():
    plane = _busy_at(512, 768)
    plane[128:256, 896:1024] = plane[512:640, 768:896]  # the same content, higher up
    plane[128:256, 384:512] = plane[512:640, 768:896]  # and further left on that row
    boxes = {tile_pictures([plane], False, 256).crop_box for _ in range(3)}
    assert boxes == {(128, 256, 384, 512)}
    shown = display_panel(plane[::4, ::4]).pixels
    assert busiest_square(shown, 4, plane.shape, CROP_SIDE) == 1 * (1024 // CROP_SIDE) + 3


def test_no_crop_when_the_thumbnail_keeps_enough_detail():
    small = blobs(1, 512)  # downscale factor 2: not over the threshold
    found = tile_pictures([small], False, 256)
    assert found.factor == 2 and found.crop_png is None and found.crop_box is None


def test_a_colour_crop_keeps_each_channel_in_its_colour():
    planes = [_busy_at(0, 128), _busy_at(0, 128)[::-1].copy(), np.full((1024, 1024), 5, np.uint16)]
    found = tile_pictures(planes, True, 256)
    crop = decode(found.crop_png)
    assert crop.shape == (2 * CROP_SIDE, 2 * CROP_SIDE, 3) and (crop[..., 2] == 128).all()


# --- the header picture (reads within the caps) ---------------------------------------------------


def _meta(root, name):
    return inspect_image(root / name, name)


def _bound(root, name):
    """The fingerprint the folder check takes with the header (the picture is of that file)."""
    return {name: Fingerprint.of(root / name)}


def test_a_picture_is_drawn_only_when_the_header_names_y_and_x(tmp_path):
    tifffile.imwrite(tmp_path / "plain.tif", np.stack([blobs(1), blobs(2)]))  # QYX: nothing named
    write_channels(tmp_path / "named.tif", ["DAPI", "Actin"], ["blobs", "filaments"])
    plain = hp.header_preview(
        tmp_path, _meta(tmp_path, "plain.tif"), bound=_bound(tmp_path, "plain.tif")
    )
    assert plain.pictures is None and plain.problem == hp.NO_YX
    named = hp.header_preview(
        tmp_path, _meta(tmp_path, "named.tif"), bound=_bound(tmp_path, "named.tif")
    )
    assert named.pictures is not None and named.text == "max projection over C (2): all 2 planes"


def test_the_projection_reads_at_most_the_plane_cap_evenly_spread(tmp_path, monkeypatch):
    stack = np.stack([np.full((32, 32), z, np.uint16) for z in range(20)])
    stack[:, 0, 0] = np.arange(20) * 100
    tifffile.imwrite(tmp_path / "z.tif", stack, metadata={"axes": "ZYX"})
    before = (tmp_path / "z.tif").read_bytes()
    reads = []
    real = hp.read_plane
    monkeypatch.setattr(hp, "read_plane", lambda *a, **k: reads.append(k) or real(*a, **k))
    found = hp.header_planes(tmp_path / "z.tif", _meta(tmp_path, "z.tif"))
    assert len(reads) == hp.PLANE_CAP == 8
    chosen = sorted(k["z"] for k in reads)
    assert chosen == [0, 3, 5, 8, 11, 14, 16, 19]
    assert found.planes[0][1, 1] == 19 and found.planes[0][0, 0] == 1900  # max of the read ones
    assert "8 of 20 planes, evenly spread" in found.text and "at most 8 planes" in found.text
    assert (tmp_path / "z.tif").read_bytes() == before  # the file is only read


def test_a_plane_over_the_pixel_cap_is_not_read_and_the_cap_is_said(tmp_path, monkeypatch):
    write_channels(tmp_path / "a.tif", ["DAPI"], ["blobs"])
    monkeypatch.setattr(hp, "PIXEL_CAP", 1000)
    monkeypatch.setattr(hp, "read_plane", lambda *a, **k: pytest.fail("read over the cap"))
    found = hp.header_preview(tmp_path, _meta(tmp_path, "a.tif"), bound=_bound(tmp_path, "a.tif"))
    assert found.pictures is None and "over the 1,000-pixel cap" in found.problem


def test_an_rgb_header_is_drawn_in_colour_and_a_read_failure_is_a_sentence(tmp_path):
    write_rgb(tmp_path / "rgb.tif")
    found = hp.header_preview(
        tmp_path, _meta(tmp_path, "rgb.tif"), bound=_bound(tmp_path, "rgb.tif")
    )
    assert found.colour and decode(found.pictures.png).shape == (64, 64, 3)
    meta = _meta(tmp_path, "rgb.tif")
    (tmp_path / "rgb.tif").write_bytes((tmp_path / "rgb.tif").read_bytes()[:400])  # truncated
    broken = hp.header_preview(tmp_path, meta, bound=_bound(tmp_path, "rgb.tif"))
    assert broken.pictures is None and broken.problem.startswith("cannot be read for a preview")


# --- in the notebook ------------------------------------------------------------------------------

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_row_fold import _text  # noqa: E402


def _checked(root, out):
    nb = PreprocessingNotebook(str(root), output_folder=str(out), describe=True)
    nb.welcome()
    nb.steps()
    nb.check_button.click()
    return nb


def test_images_found_shows_a_header_picture_per_folder_or_why_not(tmp_path):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        (root / folder).mkdir(parents=True)
    tifffile.imwrite(root / "ctrl" / "rep1" / "a.tif", large(1, 1024), metadata={"axes": "YX"})
    write_rgb(root / "ctrl" / "rep2" / "a.tif")
    tifffile.imwrite(root / "drug" / "rep1" / "a.tif", np.stack([blobs(1), blobs(2)]))
    nb = _checked(root, tmp_path / "out")
    previews = nb.label_previews
    assert len(previews.rows.children) == 4  # the header row and one row per folder
    big = previews.found["ctrl/rep1"]
    assert big.pictures.crop_png is not None and big.pictures.factor == 4
    assert "from the file header, axes not confirmed yet" in _text(
        previews.tiles["ctrl/rep1"].widget
    )
    assert previews.found["ctrl/rep2"].colour
    assert previews.found["drug/rep1"].problem == hp.NO_YX
    assert "preview after Axes (step 2)" in _text(previews.tiles["drug/rep1"].widget)
    assert nb.preflight is not None  # the check went on
    previews.tiles["ctrl/rep1"].widget.children[-1].click()  # Enlarge
    assert "ctrl/rep1" in previews.detail.children[0].value


def test_the_header_pictures_feed_nothing(tmp_path, monkeypatch):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        write_channels(root / folder / "a.tif", ["DAPI", "Actin"], ["blobs", "filaments"])
    with_pictures = _checked(root, tmp_path / "a")
    from morphobricks_preprocessing import notebook_folder_previews as previews

    monkeypatch.setattr(previews, "header_preview", lambda *a, **k: hp.HeaderPreview(None))
    without = _checked(root, tmp_path / "b")
    assert with_pictures.preflight.suggested_mapping == without.preflight.suggested_mapping
    assert with_pictures.mapping == without.mapping
    assert repr(with_pictures.draft_answers) == repr(without.draft_answers)


def test_a_big_experiment_draws_the_first_folders_and_the_rest_on_a_click(tmp_path, monkeypatch):
    from morphobricks_preprocessing import notebook_folder_previews as previews

    monkeypatch.setattr(previews, "DRAWN_AT_ONCE", 2)
    root = tmp_path / "exp"
    for folder in ("a/rep1", "a/rep2", "b/rep1", "b/rep2"):
        write_channels(root / folder / "x.tif", ["DAPI"], ["blobs"])
    shown = _checked(root, tmp_path / "out").label_previews
    assert sorted(shown.found) == ["a/rep1", "a/rep2"]  # the others are not read yet
    assert "picture not drawn yet" in _text(shown.rows)
    assert shown.more.description.strip() == "Draw the pictures of the other 2 folders"
    shown.more.click()
    assert sorted(shown.found) == ["a/rep1", "a/rep2", "b/rep1", "b/rep2"]
    assert "picture not drawn yet" not in _text(shown.rows) and len(shown.rows.children) == 5


def test_a_new_check_of_another_folder_shows_no_picture_of_the_last_one(tmp_path, monkeypatch):
    from morphobricks_preprocessing import notebook_folder_previews as previews

    monkeypatch.setattr(previews, "DRAWN_AT_ONCE", 2)
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in ("a/rep1", "a/rep2", "b/rep1", "b/rep2"):
        write_channels(first / folder / "x.tif", ["DAPI"], ["blobs"])
    for folder in ("a/rep1", "c/rep1", "c/rep2"):
        write_channels(second / folder / "x.tif", ["DAPI"], ["filaments"])
    nb = _checked(first, tmp_path / "out")
    shown = nb.label_previews
    old = shown.tiles["a/rep1"].pictures.png
    nb.folder.value, nb.output.value = str(second), str(tmp_path / "out2")
    nb.check_button.click()
    assert sorted(shown.found) == ["a/rep1", "c/rep1"]
    assert shown.tiles["a/rep1"].pictures.png != old  # read again from the new folder
    assert "b/rep1" not in _text(shown.rows) and "c/rep2" in _text(shown.rows)
    assert shown.more.description.strip() == "Draw the pictures of the other 1 folder"
    shown.more.click()
    assert sorted(shown.found) == ["a/rep1", "c/rep1", "c/rep2"] and len(shown.rows.children) == 4


def test_the_crop_says_why_it_is_missing_or_where_it_is_when_nothing_varies():
    narrow = np.random.default_rng(0).random((100, 2000)).astype(np.float32)
    found = tile_pictures([narrow], False, 256)
    assert found.crop_png is None and "under 128 pixels on a side" in found.crop_text
    wide = np.random.default_rng(0).random((300, 40000)).astype(np.float32)  # factor 157
    found = tile_pictures([wide], False, 256)
    assert found.crop_png is None and "too few pixels" in found.crop_text
    flat = np.full((1024, 1024), 7, np.uint16)
    flat[1::4, 1::4] = 9000  # native detail the thumbnail (every 4th pixel from 0) never keeps
    found = tile_pictures([flat], False, 256)
    assert found.crop_box == (0, 128, 0, 128) and "does not vary" in found.crop_text
    assert "varies most" not in found.crop_text
