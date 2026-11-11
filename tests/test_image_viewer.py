"""The notebook's one image viewer and its reader (owner, 11 November 2026).

One reader (:mod:`.image_view`) and one display component (:mod:`.notebook_viewer`) for every
step: dropdowns that narrow each other, a time and a depth slider, every channel side by side
(at most six), nothing guessed, an image read only as the revision expected.
"""

import html
import os
import re
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axes import AxisMapping
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.image_tree import ImageEntry, choices, entries_of, resolve
from morphobricks_preprocessing.image_view import ViewRequest, read_view, view_lengths
from morphobricks_preprocessing.preview import PreviewRefused
from morphobricks_preprocessing.report_view import report_html

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.notebook_viewer import ImageViewer, ViewSource  # noqa: E402
from morphobricks_preprocessing.notebook_viewers import header_roles  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402
from test_notebook_check_save import answer_all, exp  # noqa: E402, F401


def _text(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def write(path, axes, shape, **meta):
    """Write an OME-TIFF whose every plane holds its own (t, z, c) in its pixel values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros(shape, np.uint16)
    planes = [n for a, n in zip(axes, shape, strict=True) if a not in "YX"]
    for index in np.ndindex(*planes):
        value = sum(int(i) * 10**k for k, i in enumerate(reversed(index)))
        data[index] = value * 10
        data[(*index, 0, 0)] = value * 10 + 1  # not flat: the plane has a contrast
    tifffile.imwrite(
        path, data, ome=True, photometric="minisblack", metadata={"axes": axes, **meta}
    )
    return data


ENTRIES = (
    ImageEntry("ctrl", "rep1", "a", "ctrl/rep1/a/1.tif"),
    ImageEntry("ctrl", "rep1", "a", "ctrl/rep1/a/2.tif"),
    ImageEntry("ctrl", "rep2", "b", "ctrl/rep2/b/1.tif"),
    ImageEntry("drug", "rep1", "a", "drug/rep1/a/9.tif"),
)


def test_each_dropdown_offers_only_what_the_levels_above_hold():
    assert choices(ENTRIES, ()) == [
        ["ctrl", "drug"],
        ["rep1", "rep2"],
        ["a"],
        ["1.tif"][:0]
        or [
            "ctrl/rep1/a/1.tif",
            "ctrl/rep1/a/2.tif",
        ],
    ]
    assert choices(ENTRIES, ("drug",)) == [["ctrl", "drug"], ["rep1"], ["a"], ["drug/rep1/a/9.tif"]]
    assert choices(ENTRIES, ("ctrl", "rep2"))[2:] == [["b"], ["ctrl/rep2/b/1.tif"]]
    # A value no longer offered falls back to the first: no combination without an image.
    assert resolve(ENTRIES, ("drug", "rep2", "b", None)).image == "drug/rep1/a/9.tif"
    assert resolve(ENTRIES, ("ctrl", "rep1", "a", "ctrl/rep1/a/2.tif")).image.endswith("2.tif")
    assert resolve((), ()) is None


def test_the_entries_are_the_leaf_folders_images_without_the_excluded():
    leaves = [
        SimpleNamespace(labels=("c", "r", "single"), images=("c/r/b.tif", "c/r/a.tif")),
        SimpleNamespace(labels=("d", "r", "single"), images=("d/r/a.tif",)),
    ]
    found = entries_of(leaves, excluded=["c/r/b.tif"])
    assert [e.image for e in found] == ["c/r/a.tif", "d/r/a.tif"]
    assert found[0].path == ("c", "r", "single", "c/r/a.tif") and found[0].name == "a.tif"


@pytest.mark.parametrize(
    ("axes", "shape", "at", "expected"),
    [
        ("ZCYX", (5, 2, 8, 9), {}, (2, None)),  # the middle slice first
        ("ZCYX", (5, 2, 8, 9), {"z": 4}, (4, None)),
        ("TCYX", (3, 2, 8, 9), {}, (None, 0)),  # the first time point first
        ("TCYX", (3, 2, 8, 9), {"t": 2}, (None, 2)),
        ("TZCYX", (3, 5, 2, 8, 9), {"t": 1, "z": 3}, (3, 1)),
        ("CYX", (2, 8, 9), {"z": 3, "t": 2}, (None, None)),  # no such axis: no coordinate
    ],
)
def test_a_view_is_every_channel_of_one_slice_and_time_point(tmp_path, axes, shape, at, expected):
    data = write(tmp_path / "a.tif", axes, shape)
    mapping = AxisMapping(axes, tuple(axes))
    view = read_view(tmp_path / "a.tif", mapping, shape, ViewRequest(**at))
    assert (view.z, view.t) == expected and view.plane == (8, 9) and view.step == 1
    assert [tile.c for tile in view.tiles] == [0, 1] and view.shown_text == "all 2 channels"
    lengths = view_lengths(shape, mapping)
    assert lengths == {r: shape[axes.index(r)] if r in axes else 1 for r in "ZCT"}
    # Each tile is that plane: read again directly and compare what was drawn.
    index = tuple(
        {"T": view.t, "Z": view.z}[a] if a in "TZ" else slice(None) for a in axes if a not in "YX"
    )
    planes = data[index]
    assert planes.shape == (2, 8, 9) and planes[0, 1, 1] != planes[1, 1, 1]


def test_at_most_six_channels_are_drawn_and_the_view_says_so(tmp_path):
    write(tmp_path / "a.tif", "CYX", (9, 8, 8))
    view = read_view(tmp_path / "a.tif", AxisMapping("CYX", tuple("CYX")), (9, 8, 8))
    assert len(view.tiles) == 6 and view.channels == 9
    assert view.shown_text == "6 of 9 channels shown"
    one = read_view(tmp_path / "a.tif", AxisMapping("CYX", tuple("ZYX")), (9, 8, 8))
    assert [t.c for t in one.tiles] == [None] and one.shown_text == "the only channel"


def test_a_view_is_refused_for_another_revision_and_without_y_and_x(tmp_path):
    path = tmp_path / "a.tif"
    write(path, "CYX", (2, 8, 8))
    mapping = AxisMapping("CYX", tuple("CYX"))
    bound = Fingerprint.of(path)
    assert read_view(path, mapping, (2, 8, 8), ViewRequest(expected=bound)).tiles
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**10))
    with pytest.raises(PreviewRefused, match="changed since the folder check read it"):
        read_view(path, mapping, (2, 8, 8), ViewRequest(expected=bound), name="a.tif")
    with pytest.raises(ValueError, match="without a Y and an X"):
        read_view(path, AxisMapping("CYX", ("C", "Y", None)), (2, 8, 8))


def test_header_roles_are_given_only_when_the_header_names_every_axis(tmp_path):
    from morphobricks_preprocessing.metadata import inspect_image

    write(tmp_path / "named.tif", "TCYX", (2, 2, 8, 8))
    mapping, why = header_roles(inspect_image(tmp_path / "named.tif", "named.tif"))
    assert mapping.roles == ("T", "C", "Y", "X") and why == ""
    tifffile.imwrite(
        tmp_path / "plain.tif", np.zeros((4, 8, 8), np.uint16), photometric="minisblack"
    )
    mapping, why = header_roles(inspect_image(tmp_path / "plain.tif", "plain.tif"))
    assert mapping is None and "preview after Axes" in why


# --- the widget ----------------------------------------------------------------------------------


def viewer_of(tmp_path, axes="TZCYX", shape=(3, 5, 2, 8, 8)):
    for entry in ENTRIES:
        write(tmp_path / entry.image, axes, shape)
    mapping = AxisMapping(axes, tuple(axes))
    viewer = ImageViewer(lambda image: ViewSource(tmp_path / image, mapping, shape, note="n"))
    viewer.set_entries(ENTRIES)
    return viewer


def test_the_viewer_has_plain_dropdowns_and_sliders_and_draws_by_itself(tmp_path):
    viewer = viewer_of(tmp_path)
    assert viewer.image == ENTRIES[0].image and viewer.view is not None
    assert [p.description for p in viewer.pickers] == [
        "Condition",
        "Biological repeat",
        "Technical repeat",
        "Image",
    ]
    assert all(ui.is_visible(p) and not p.disabled for p in viewer.pickers)
    t, z = viewer.sliders["T"], viewer.sliders["Z"]
    assert (t.max, t.value, z.max, z.value) == (2, 0, 4, 2)
    assert ui.is_visible(t) and ui.is_visible(z)
    said = _text(viewer.caption.value)
    assert "time point t=0 of 3" in said and "depth slice z=2 of 5" in said and "8 × 8 px" in said
    z.value = 4
    t.value = 1
    assert (viewer.view.z, viewer.view.t) == (4, 1)
    assert "time point t=1 of 3 · depth slice z=4 of 5" in _text(viewer.caption.value)


def test_choosing_a_condition_narrows_the_other_dropdowns_and_shows_its_image(tmp_path):
    viewer = viewer_of(tmp_path)
    viewer.sliders["Z"].value = 4
    viewer.pickers[0].value = "drug"
    assert viewer.image == "drug/rep1/a/9.tif" and list(viewer.pickers[1].options) == ["rep1"]
    assert viewer.sliders["Z"].value == 2  # another image opens on its middle slice
    viewer.pickers[0].value = "ctrl"
    viewer.pickers[1].value = "rep2"
    assert viewer.image == "ctrl/rep2/b/1.tif" and list(viewer.pickers[2].options) == ["b"]
    viewer.select("ctrl/rep1/a/2.tif")
    assert [p.value for p in viewer.pickers] == ["ctrl", "rep1", "a", "ctrl/rep1/a/2.tif"]
    viewer.select("not/listed.tif")
    assert viewer.image == "ctrl/rep1/a/2.tif"


def test_sliders_show_only_for_axes_the_image_has_and_a_level_of_one_is_not_a_choice(tmp_path):
    entries = (ImageEntry("ctrl", "single", "single", "ctrl/1.tif"),)
    write(tmp_path / "ctrl/1.tif", "CYX", (2, 8, 8))
    mapping = AxisMapping("CYX", tuple("CYX"))
    viewer = ImageViewer(lambda image: ViewSource(tmp_path / image, mapping, (2, 8, 8)))
    assert not ui.is_visible(viewer.widget)  # nothing to show yet
    viewer.set_entries(entries)
    assert ui.is_visible(viewer.widget)
    assert not any(ui.is_visible(s) for s in viewer.sliders.values())
    assert [ui.is_visible(p) for p in viewer.pickers] == [False, False, False, True]


def test_a_picture_that_cannot_be_drawn_is_a_sentence_and_the_draw_is_reported(tmp_path):
    drawn = []
    viewer = ImageViewer(lambda image: ViewSource(tmp_path / image, problem="no roles yet"))
    viewer.on_draw(lambda: drawn.append(viewer.view))
    viewer.set_entries(ENTRIES)
    assert viewer.view is None and viewer.drawn is None and not viewer.tiles.children
    assert "No picture of ctrl/rep1/a/1.tif" in viewer.caption.value
    assert "no roles yet" in viewer.caption.value and drawn == [None]
    gone = ImageViewer(
        lambda image: ViewSource(tmp_path / image, AxisMapping("YX", ("Y", "X")), (4, 4))
    )
    gone.set_entries(ENTRIES)  # the file does not exist: a sentence, not a traceback
    assert gone.view is None and "No picture of" in gone.caption.value


def test_the_captions_follow_the_source_without_reading_again(tmp_path, monkeypatch):
    from morphobricks_preprocessing import image_view

    labels = ["one"]
    write(tmp_path / ENTRIES[0].image, "CYX", (2, 8, 8))
    mapping = AxisMapping("CYX", tuple("CYX"))
    viewer = ImageViewer(
        lambda image: ViewSource(tmp_path / image, mapping, (2, 8, 8), labels=tuple(labels * 2))
    )
    viewer.set_entries(ENTRIES[:1])
    assert "one" in viewer.tiles.children[0].children[0].value
    monkeypatch.setattr(image_view, "read_plane", lambda *a, **k: pytest.fail("read again"))
    labels[0] = "two"
    viewer.relabel()
    assert "two" in viewer.tiles.children[1].children[0].value


# --- in the notebook -----------------------------------------------------------------------------


def stack_experiment(tmp_path, axes, shape, **meta):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        write(root / folder / "a.tif", axes, shape, **meta)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.scan_button.click()
    return nb


@pytest.mark.parametrize(
    ("axes", "shape", "sliders"),
    [
        ("ZCYX", (5, 2, 8, 8), {"Z": True, "T": False}),
        ("TCYX", (4, 2, 8, 8), {"Z": False, "T": True}),
        ("TZCYX", (4, 5, 2, 8, 8), {"Z": True, "T": True}),
    ],
)
def test_every_step_s_viewer_has_the_sliders_of_a_stack_or_a_time_lapse(
    tmp_path, axes, shape, sliders
):
    nb = stack_experiment(tmp_path, axes, shape)
    for viewer in (nb.scan_viewer,):  # the quick scan, from the one header
        assert {r: ui.is_visible(s) for r, s in viewer.sliders.items()} == sliders
    nb.check_button.click()
    for viewer in (nb.scan_viewer, nb.axes_viewer):  # Images after the check, and the example
        assert viewer.view is not None
        assert {r: ui.is_visible(s) for r, s in viewer.sliders.items()} == sliders


def test_the_channels_and_check_viewers_wait_for_the_confirmed_roles(tmp_path):
    nb = stack_experiment(tmp_path, "TCYX", (3, 2, 8, 8), Channel={"Name": ["DAPI", "Actin"]})
    nb.check_button.click()
    assert nb.channels_viewer.entries == () and not ui.is_visible(nb.channels_look)
    nb.quick_confirm.click()
    for viewer, card in ((nb.channels_viewer, nb.channels_look), (nb.check_viewer, nb.check_look)):
        assert ui.is_visible(card) and viewer.view is not None
        assert ui.is_visible(viewer.sliders["T"])
        assert "read with the confirmed axis roles" in viewer.caption.value
        names = [_text(tile.children[0].value) for tile in viewer.tiles.children]
        assert names == ["c=0 ‘DAPI’", "c=1 ‘Actin’"]  # each image's own header names


def test_the_tag_grid_and_the_folders_found_draw_no_picture_of_their_own(tmp_path):
    import ipywidgets as widgets

    nb = stack_experiment(tmp_path, "CYX", (2, 8, 8))
    nb.check_button.click()
    nb.quick_confirm.click()
    nb.type_line.apply.click()

    def images(widget):
        if isinstance(widget, widgets.Image):
            yield widget
        for child in getattr(widget, "children", ()):
            yield from images(child)

    assert not list(images(nb.channel_grid.widget)) and not list(images(nb.labels_card))
    assert nb.label_previews.found == {}  # the check drew nothing per folder
    assert ui.is_visible(nb.labels_table) and "ctrl" in nb.labels_table.value


# --- the report, formatted -----------------------------------------------------------------------

REPORT = {
    "step": "run",
    "status": "written",
    "experiment_root": "/data/exp",
    "output_folder": "/data/out",
    "groups": [
        {"condition": "ctrl", "biological_repeat": "r1", "technical_repeat": "single", "images": 2}
    ],
    "channels": 2,
    "channel_names": ["DAPI", None],
    "images": {
        "ctrl/r1/a.tif": {
            "axes": "CYX",
            "roles": "CYX",
            "axes_from": "header",
            "pixel_sizes": {"X": {"value": 0.1, "unit": "micrometer", "source": "header"}},
        }
    },
    "design": [{"condition": "ctrl", "biological_repeat": "r1", "repeat_group": "r1",
                "confirmed": True, "control": False}],
    "masks": {"types": {"nuclei": {"paired": 2, "images_without": []}}},
    "mode": "restart",
    "reused": [],
    "recomputed": {"ctrl/r1/a.tif": "no earlier run"},
    "replaced": {"discovery_report.txt": "replaced"},
    "issues": [
        {"code": "non_square_pixels", "severity": "warning", "message": "x" * 300,
         "paths": ["ctrl/r1/a.tif"], "action": "State <both> sizes."}
    ],
}  # fmt: skip


def test_the_report_is_headings_and_tables_with_the_details_folded():
    page = report_html(REPORT, "plain <text>")
    for heading in ("Folders", "Axes and pixel sizes", "Design", "Masks", "What the run did",
                    "Checks", "Plain text"):  # fmt: skip
        assert f">{heading}</div>" in page, heading
    assert page.count("<table") >= 6 and "<pre" in page
    text = _text(page)
    assert "Found 2 images in 1 condition, 1 folder" in text and "Channels 2 (DAPI)" in text
    assert "0 errors, 1 warning" in text and "1 of 1 repeat row confirmed" in text
    assert "0.1 micrometer (header)" in text and "Y" in text
    # A long message is folded behind its first words; nothing of it is lost; all is escaped.
    assert "<b>warning · non square pixels</b>: " + "x" * 110 + "…</summary>" in page
    assert (
        "x" * 300 in page and "State &lt;both&gt; sizes." in page and "plain &lt;text&gt;" in page
    )
    assert page.count("<details") >= 7
    assert report_html({}) != ""  # a report with nothing in it is still a page


def test_the_notebook_shows_the_run_report_formatted_in_its_one_fold(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment() and nb.run().written
    view = nb.run_report
    assert view.box not in view.widget.children  # no fold inside the Run report fold
    assert "mb-table" in view.text.value and ">Checks</div>" in view.text.value
    assert "The report as plain text" in view.text.value
