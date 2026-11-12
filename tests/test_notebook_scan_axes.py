"""The new axes flow (owner, 11 November 2026) and the pixel sizes that differ in one line.

The quick scan proposes the axes of its one image from that image's header; the user changes
a role right there and sees the pictures redrawn; what is left there is the proposal the folder
check validates against every image, and the Axes step confirms for the whole experiment.
Nothing is confirmed without the click on Axes.
"""

import html
import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.pixel_size_summary import (
    combination_rows,
    spread_line,
    summary_line,
    xy_groups,
)
from morphobricks_preprocessing.recorded_sizes import RecordedSize

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_render import issues_brief_html  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.validation import warning  # noqa: E402


def _text(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def scanned(tmp_path, shape=(4, 3, 16, 16), axes="ZCYX", *, named=True, sizes=None):
    """Write two folders of two images each and scan them (no header check yet)."""
    root = tmp_path / "exp"
    rng = np.random.default_rng(3)
    for number, folder in enumerate(("ctrl/rep1", "drug/rep1")):
        for name in ("a.tif", "b.tif"):
            path = root / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = rng.integers(0, 4000, shape, dtype=np.uint16)
            if not named:
                tifffile.imwrite(path, data, photometric="minisblack")
                continue
            meta = {"axes": axes}
            if sizes is not None:
                x, y = sizes[2 * number + (name == "b.tif")]
                meta |= {"PhysicalSizeX": x, "PhysicalSizeY": y}
            tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=meta)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.scan_button.click()
    return nb


def test_the_scan_proposes_the_axes_of_its_image_from_the_header(tmp_path):
    nb = scanned(tmp_path)
    assert ui.is_visible(nb.scan_roles_card)
    assert [box.value for box in nb._scan_boxes] == ["Z", "C", "Y", "X"]
    labels = _text(" ".join(cell.children[0].value for cell in nb.scan_roles_row.children))
    assert "Z (depth) in the file · length 4" in labels and "C (channel)" in labels
    assert "proposed from its header" in _text(nb.scan_roles_card.children[0].value)
    assert nb._scan_roles == {}  # a proposal left alone is the header's: nothing is held
    assert "from the file header, axes not confirmed yet" in nb.scan_viewer.caption.value
    assert nb.preflight is None and nb.mapping is None and nb.confirmed_mapping is None


def test_a_role_changed_at_the_scan_redraws_the_pictures_right_there(tmp_path):
    nb = scanned(tmp_path)
    viewer = nb.scan_viewer
    assert len(viewer.view.tiles) == 3 and ui.is_visible(viewer.sliders["Z"])
    nb._scan_boxes[0].value = "C"  # the stack is really four channels ...
    assert "More than one axis has the role C" in viewer.caption.value and viewer.view is None
    nb._scan_boxes[1].value = "Z"  # ... of three slices
    assert len(viewer.view.tiles) == 4 and viewer.sliders["Z"].max == 2
    assert "read with the roles chosen above, not confirmed yet" in viewer.caption.value
    assert nb._scan_roles["ZCYX"].roles == ("C", "Z", "Y", "X")
    assert nb.confirmed_mapping is None and nb.preflight is None  # still only a proposal


def test_a_plain_stack_gets_its_picture_once_the_user_says_what_its_dimensions_are(tmp_path):
    nb = scanned(tmp_path, shape=(3, 16, 16), named=False)
    viewer = nb.scan_viewer
    assert viewer.view is None and "preview after Axes" in viewer.caption.value
    assert [box.value for box in nb._scan_boxes] == ["-", "Y", "X"]  # nothing guessed
    nb._scan_boxes[0].value = "C"
    assert viewer.view is not None and len(viewer.view.tiles) == 3


def test_the_roles_left_at_the_scan_are_the_proposal_the_check_validates_on_all_images(tmp_path):
    nb = scanned(tmp_path, shape=(3, 16, 16), named=False)
    nb._scan_boxes[0].value = "C"
    nb.check_button.click()
    assert nb.mapping.roles == ("C", "Y", "X")
    assert [box.value for box in nb.role_dropdowns] == ["C", "Y", "X"]  # the Axes step starts here
    line = _text(nb.scan_axes_line.value)
    assert line.startswith("Axes C, Y, X (chosen by you on the example): checked against all 4")
    assert "can still be changed there" in line or "can be changed" in line
    assert nb.confirmed_mapping is None  # the check validates; only Axes confirms
    assert not ui.is_visible(nb.scan_roles_card)  # after the check the roles are on Axes
    assert nb.axes_viewer.view is not None and len(nb.axes_viewer.view.tiles) == 3
    # The plain stack names no axis: the tick and Confirm axis roles, as before.
    assert not ui.is_visible(nb.quick_card)
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.confirmed_mapping.roles == ("C", "Y", "X")


def test_roles_that_contradict_the_headers_are_named_after_the_check_and_not_offered(tmp_path):
    nb = scanned(tmp_path)
    nb._scan_boxes[0].value = "T"  # the header says depth; the user says time
    nb.check_button.click()
    assert nb.mapping.roles == ("T", "C", "Y", "X")
    line = _text(nb.scan_axes_line.value)
    assert "checked against all 4 images, they do not fit (axes contradict header)" in line
    assert not ui.is_visible(nb.quick_card) and nb.confirm_button.disabled
    nb.role_dropdowns[0].value = "Z"  # changed on Axes: the line on Images follows
    assert "Axes Z, C, Y, X (proposed from the image headers)" in _text(nb.scan_axes_line.value)


def test_header_roles_left_alone_are_offered_for_one_click_and_say_they_can_be_changed(tmp_path):
    nb = scanned(tmp_path, shape=(3, 16, 16), axes="CYX")
    nb.check_button.click()
    line = _text(nb.scan_axes_line.value)
    assert line == (
        "Axes C, Y, X (proposed from the image headers): checked against all 4 images, they "
        "fit every one. Confirm them on Axes (step 2); they can still be changed there."
    )
    assert ui.is_visible(nb.quick_card) and "They can be changed above" in nb.quick_text.value
    page = nb._stepper.pages[1].children[1].children[1].children
    assert nb.axes_change_note in page and "The axes can be changed" in nb.axes_change_note.value
    nb.quick_confirm.click()
    assert nb.confirmed_mapping.roles == ("C", "Y", "X")
    nb._stepper.go(0)  # Images now reads with the confirmed roles
    assert "read with the confirmed axis roles" in nb.scan_viewer.caption.value


def test_a_new_scan_forgets_the_roles_of_the_one_before(tmp_path):
    nb = scanned(tmp_path)
    nb._scan_boxes[0].value = "T"
    nb.scan_button.click()
    assert nb._scan_roles == {} and [box.value for box in nb._scan_boxes][0] == "Z"


# --- pixel sizes that differ ---------------------------------------------------------------------


def _sizes(x, y):
    return (
        RecordedSize("X", x, "micrometer", "header"),
        RecordedSize("Y", y, "micrometer", "header"),
    )


def test_many_slightly_different_sizes_are_one_line_with_their_range_and_spread():
    values = [0.10047, 0.10049, 0.1005, 0.10052, 0.10054]
    sizes = {
        f"c/r{i}/img{j}.tif": _sizes(values[i], values[(i + j) % 5])
        for i in range(5)
        for j in range(5)
    }
    groups = xy_groups(sizes)
    kind, line = summary_line(groups)
    assert kind == "warn" and line == (
        "Pixel sizes differ slightly: 0.10047–0.10054 µm (spread 0.07 %) across 25 images, "
        "25 combinations."
    )
    rows = combination_rows(groups)
    assert len(rows) == 25 and rows[0][1] == "1" and rows[0][3] == "from the headers"
    assert all(len(row) == 4 for row in rows)


def test_the_range_line_is_only_for_three_or_more_calibrated_sizes_in_one_unit():
    two = xy_groups({"a/1.tif": _sizes(0.1, 0.1), "b/1.tif": _sizes(0.2, 0.2)})
    assert spread_line(two) is None and summary_line(two)[1].startswith("Headers disagree")
    wide = xy_groups({f"f{i}/1.tif": _sizes(v, v) for i, v in enumerate((0.1, 0.2, 0.4))})
    assert spread_line(wide).startswith("Pixel sizes differ: 0.1–0.4 µm (spread 120 %)")
    none = dict({f"f{i}/1.tif": _sizes(v, v) for i, v in enumerate((0.1, 0.2))})
    none["g/1.tif"] = (RecordedSize("X"), RecordedSize("Y"))
    assert spread_line(xy_groups(none)) is None  # an image without a size is named, not ranged
    mixed = dict(none)
    mixed["g/1.tif"] = (
        RecordedSize("X", 100.0, "nanometer", "header"),
        RecordedSize("Y", 100.0, "nanometer", "header"),
    )
    assert spread_line(xy_groups(mixed)) is None  # two units: nothing is converted here


def test_the_notebook_says_differing_sizes_in_one_line_with_the_table_folded(tmp_path):
    sizes = [(0.10047, 0.10049), (0.1005, 0.10052), (0.10054, 0.10047), (0.10049, 0.1005)]
    nb = scanned(tmp_path, shape=(3, 16, 16), axes="CYX", sizes=sizes)
    nb.check_button.click()
    assert _text(nb.pixel_line.value) == (
        "Pixel sizes differ slightly: 0.10047–0.10054 µm (spread 0.07 %) across 4 images, "
        "4 combinations."
    )
    assert nb.pixel_units.selected_index is None  # the per-unit tables do not open by themselves
    folded = nb.pixel_differs.value
    assert folded.startswith("<details") and "The 4 different sizes" in folded
    assert "0.10047 × 0.10049 µm" in _text(folded) and "Nothing is chosen for you" in _text(folded)
    assert not (tmp_path / "out" / "calibration.csv").exists()  # never chosen silently
    recorded = {
        s.role: (s.value, s.source) for s in nb.preflight.calibration.sizes["ctrl/rep1/a.tif"]
    }
    assert recorded["X"] == (0.10047, "header")
    # The explicit choice: one size for all, shown before it replaces, recorded as stated.
    nb.pixel_x.value = "0.1005"
    nb.pixel_apply.click()
    assert (
        ui.is_visible(nb.pixel_replace_box) and not (tmp_path / "out" / "calibration.csv").exists()
    )
    nb.pixel_replace_yes.click()
    text = (tmp_path / "out" / "calibration.csv").read_text(encoding="utf-8")
    assert ".,X,0.1005,micrometer" in text and ".,Y,0.1005,micrometer" in text
    assert _text(nb.pixel_line.value) == (
        "Pixel size: 0.1005 × 0.1005 µm (stated by you, same in all 4 images)."
    )
    assert nb.pixel_differs.value == ""


def test_a_message_that_lists_every_image_is_cut_and_kept_whole_one_click_away():
    listed = "; ".join(f"folder/image_{i}.tif: X 0.1, Y 0.2 micrometres" for i in range(40))
    issue = warning(
        "non_square_pixels", f"40 images have pixels that are not square ({listed}). Bricks refuse."
    )
    page = issues_brief_html([issue])
    shown = page.split("<details>")[0]
    assert len(_text(shown)) < 260 and "…" in shown
    assert "image_39.tif" in page and "Bricks refuse." in page  # nothing is left out
