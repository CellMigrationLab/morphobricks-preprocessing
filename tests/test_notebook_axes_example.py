"""The axes step shows the data as the roles read it, and the pixel size in one line.

Owner's test of v0.5.0 (10 November 2026): (e) an example of one image as interpreted by the
chosen roles, drawn by itself and redrawn on every role change, with the explicit confirmation
kept; (f) the pixel size handled with the axes: one line for the whole experiment, one input
when the headers give none, a per-unit menu, and never a silent choice when the headers differ.
"""

import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axis_example import (
    CHANNEL_TILES,
    STEP_TILES,
    example_rows,
    spread,
)
from morphobricks_preprocessing.pixel_size_summary import (
    size_words,
    source_words,
    summary_line,
    unit_rows,
    xy_groups,
)
from morphobricks_preprocessing.recorded_sizes import RecordedSize

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing import image_view  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_plots import example_png, tile_label  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# --- which planes the example shows --------------------------------------------------------------


def test_spread_keeps_the_ends_and_never_more_than_asked():
    assert spread(3, 6) == [0, 1, 2]
    assert spread(9, 4) == [0, 3, 5, 8]
    assert spread(100, 4) == [0, 33, 66, 99]
    assert spread(5, 1) == [0] and spread(1, 4) == [0]


def test_channels_get_one_tile_each_labelled_by_their_number():
    (row,) = example_rows({"C": 3}, {"C": 0})
    assert row.role == "C" and row.title == "Channels (C)"
    assert [tile.label for tile in row.tiles] == ["c=0", "c=1", "c=2"]
    assert [tile.at() for tile in row.tiles] == [{"c": 0}, {"c": 1}, {"c": 2}]


def test_time_points_and_slices_get_a_few_labelled_tiles():
    (row,) = example_rows({"T": 30}, {"T": 0})
    assert row.title == f"Time points (T): {STEP_TILES} of 30"
    assert [tile.label for tile in row.tiles] == ["t=0", "t=10", "t=19", "t=29"]
    (row,) = example_rows({"Z": 3}, {"Z": 1})
    assert row.title == "Depth slices (Z)" and [t.label for t in row.tiles] == ["z=0", "z=1", "z=2"]


def test_each_row_is_read_at_the_sliders_position_of_the_other_axes():
    channels, times = example_rows({"C": 2, "T": 5}, {"C": 1, "T": 4})
    assert [tile.at() for tile in channels.tiles] == [{"c": 0, "t": 4}, {"c": 1, "t": 4}]
    assert [tile.at() for tile in times.tiles] == [{"c": 1, "t": t} for t in (0, 1, 3, 4)]
    many = example_rows({"C": 20}, {"C": 0})[0]
    assert len(many.tiles) == CHANNEL_TILES and many.title == f"Channels (C): {CHANNEL_TILES} of 20"
    assert example_rows({"C": 2}, {"C": 9})[0].tiles[0].at() == {"c": 0}  # a slider is clamped


def test_an_image_without_channels_time_or_depth_is_one_plane():
    (row,) = example_rows({}, {})
    assert row.role == "" and len(row.tiles) == 1 and row.tiles[0].at() == {}
    (row,) = example_rows({"C": 1}, {"C": 0})  # a length-1 axis is no row of its own
    assert row.role == "" and row.tiles[0].at() == {"c": 0}


def test_the_picture_labels_each_tile_with_its_size():
    assert tile_label("c=1", (2048, 1024)) == "c=1 · 2048 × 1024 px"
    assert tile_label("", (3, 2048)) == "3 × 2048 px (height × width)"
    rng = np.random.default_rng(0)
    plane = rng.integers(0, 4000, (64, 64), dtype=np.uint16)
    strip = rng.integers(0, 4000, (3, 64), dtype=np.uint16)  # a length-3 axis taken as Y
    png = example_png(
        [("Channels (C)", [("c=0", plane), ("c=1", plane)]), ("The plane", [("", strip)])]
    )
    assert png[:4] == b"\x89PNG"


# --- the pixel size in words ---------------------------------------------------------------------


def _sizes(value, unit="micrometer", source="header"):
    return (RecordedSize("X", value, unit, source), RecordedSize("Y", value, unit, source))


NONE = (RecordedSize("X"), RecordedSize("Y"))


def test_one_size_for_every_image_is_one_line():
    sizes = {f"ctrl/rep{i}/a.tif": _sizes(0.108) for i in range(4)}
    groups = xy_groups(sizes)
    assert summary_line(groups) == (
        "ok",
        "Pixel size: 0.108 × 0.108 µm (from the headers, same in all 4 images).",
    )
    stated = xy_groups({k: _sizes(0.5, source="stated") for k in sizes})
    assert summary_line(stated)[1] == (
        "Pixel size: 0.5 × 0.5 µm (stated by you, same in all 4 images)."
    )


def test_no_size_and_stated_pixels_are_said_as_uncalibrated_not_guessed():
    kind, line = summary_line(xy_groups({"a.tif": NONE, "b.tif": NONE}))
    assert kind == "info"
    assert line == (
        "Pixel size: none (no size in the headers and none stated: uncalibrated; 2 images)."
    )
    pixels = (RecordedSize("X", source="pixels"), RecordedSize("Y", source="pixels"))
    assert (
        "pixels (stated by you as deliberately uncalibrated; 1 image)"
        in summary_line(xy_groups({"a.tif": pixels}))[1]
    )


def test_headers_that_disagree_are_named_with_counts_and_folders_never_one_chosen():
    sizes = {f"c1/rep1/w{i}/a.tif": _sizes(0.108) for i in range(3)}
    sizes |= {f"c2/rep2/w1/{name}.tif": _sizes(0.216) for name in "ab"}
    kind, line = summary_line(xy_groups(sizes))
    assert kind == "warn"
    assert line == (
        "Headers disagree: 0.108 × 0.108 µm in 3 images, 0.216 × 0.216 µm in 2 (c2/rep2/w1)."
    )
    sizes["c3/rep1/a.tif"] = NONE
    line = summary_line(xy_groups(sizes))[1]
    assert line.startswith("Headers disagree:") and "none in 1 (c3/rep1)" in line
    sizes["c2/rep2/w1/a.tif"] = _sizes(0.3, source="stated")
    assert summary_line(xy_groups(sizes))[1].startswith("Pixel sizes differ:")


def test_size_and_source_words_keep_x_and_y_apart_when_they_differ():
    group = xy_groups(
        {"a.tif": (RecordedSize("X", 0.2, "micrometer", "header"), RecordedSize("Y"))}
    )[0]
    assert size_words(group) == "X 0.2 µm, Y none"
    assert source_words(group) == "X: the header, Y: none"
    other = xy_groups(
        {
            "a.tif": (
                RecordedSize("X", 0.2, "micrometer", "header"),
                RecordedSize("Y", 0.3, "micrometer", "header"),
            )
        }
    )[0]
    assert size_words(other) == "0.2 × 0.3 µm"


def test_unit_rows_give_each_folder_its_size_and_say_when_a_folder_is_mixed():
    sizes = {"c1/r1/a.tif": _sizes(0.1), "c1/r1/b.tif": _sizes(0.1), "c2/r1/a.tif": NONE}
    sizes |= {"c3/r1/a.tif": _sizes(0.1), "c3/r1/b.tif": _sizes(0.2)}
    rows = dict((folder, (size, source)) for folder, size, source in unit_rows(sizes))
    assert rows["c1/r1"] == ("0.1 × 0.1 µm", "from the headers")
    assert rows["c2/r1"][0] == "none"
    assert rows["c3/r1"][0] == "differs between its images"


# --- the notebook --------------------------------------------------------------------------------


def experiment(tmp_path, sizes=None, shape=(2, 3, 16, 16), axes="TCYX"):
    """Write two folders of one image each; ``sizes`` maps a folder to its header pixel size."""
    root = tmp_path / "exp"
    rng = np.random.default_rng(1)
    for folder in ("ctrl/rep1", "drug/rep1"):
        path = root / folder / "a.tif"
        path.parent.mkdir(parents=True)
        metadata = {"axes": axes}
        size = (sizes or {}).get(folder)
        if size is not None:
            metadata |= {"PhysicalSizeX": size, "PhysicalSizeY": size}
        data = rng.integers(0, 4000, shape, dtype=np.uint16)
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    return nb


def test_the_example_is_drawn_by_the_check_and_nothing_is_confirmed(tmp_path):
    nb = experiment(tmp_path)
    viewer = nb.axes_viewer  # the example is the notebook's one viewer
    assert viewer.view is not None and viewer.tiles.children
    assert nb._preview_drawn == nb.mapping
    assert not ui.is_visible(nb.preview_button)  # no "Show preview" to find first
    assert nb.confirmed_mapping is None and not nb.preview_checked.value  # never by itself
    assert "not confirmed yet" in viewer.caption.value
    assert "ctrl/rep1/a.tif" in viewer.caption.value


def test_the_example_reads_every_channel_of_one_plane(tmp_path, monkeypatch):
    nb = experiment(tmp_path, shape=(5, 3, 16, 16))
    read = []
    real = image_view.read_plane

    def spy(path, mapping, **coords):
        read.append({k: coords[k] for k in ("z", "c", "t")})
        return real(path, mapping, **coords)

    monkeypatch.setattr(image_view, "read_plane", spy)
    nb._render_preview()
    assert read == [{"z": None, "c": c, "t": 0} for c in range(3)]
    nb.axes_viewer.sliders["T"].value = 3  # another time point: the same channels of it
    assert read[3:] == [{"z": None, "c": c, "t": 3} for c in range(3)]


def test_the_example_is_redrawn_for_every_role_change_and_the_tick_taken_back(tmp_path):
    nb = experiment(tmp_path)
    nb.preview_checked.button.click()
    viewer = nb.axes_viewer
    before = viewer.view
    assert ui.is_visible(viewer.sliders["T"]) and not ui.is_visible(viewer.sliders["Z"])
    nb.role_dropdowns[0].value = "Z"  # time read as depth: the depth slider, the middle slice
    assert viewer.view is not None and viewer.view is not before
    assert ui.is_visible(viewer.sliders["Z"]) and not ui.is_visible(viewer.sliders["T"])
    assert nb._preview_drawn == nb.mapping and nb.mapping.roles[0] == "Z"
    assert not nb.preview_checked.value  # the tick was for the other roles
    nb.role_dropdowns[0].value = "T"
    assert nb._preview_drawn.roles[0] == "T"
    nb.role_dropdowns[1].value = "-"  # the channel axis without a role: refused
    assert nb.confirm_button.disabled and nb.confirmed_mapping is None


def test_another_image_or_plane_is_chosen_in_the_page_not_in_a_fold(tmp_path):
    """Owner's test of v0.5.1: the folded "Another image, or other planes" could not be used."""
    nb = experiment(tmp_path)
    viewer = nb.axes_viewer
    shown = _widgets_of(nb.preview_card)
    assert not any(isinstance(w, widgets.Accordion) for w in shown)  # nothing folded
    assert nb.preview_more not in shown and nb.preview_file not in shown
    for control in (*viewer.pickers, *viewer.sliders.values()):
        assert control in shown and not control.disabled
    viewer.pickers[0].value = "drug"
    assert "drug/rep1/a.tif" in viewer.caption.value and nb._preview_drawn == nb.mapping
    viewer.sliders["T"].value = 1
    assert "time point t=1 of 2" in viewer.caption.value


def _widgets_of(box):
    found = [box]
    for child in getattr(box, "children", ()):
        found += _widgets_of(child)
    return found


def test_the_pixel_size_of_the_headers_is_one_line_with_the_form_folded(tmp_path):
    nb = experiment(tmp_path, {"ctrl/rep1": 0.325, "drug/rep1": 0.325})
    assert _text(nb.pixel_line.value) == (
        "Pixel size: 0.325 × 0.325 µm (from the headers, same in all 2 images)."
    )
    assert ui.is_visible(nb.pixel_change) and not ui.is_visible(nb.pixel_form)
    assert nb.pixel_units.selected_index is None
    nb.pixel_change.click()
    assert ui.is_visible(nb.pixel_form)


def test_without_a_size_in_the_headers_there_is_one_input_for_the_whole_experiment(tmp_path):
    nb = experiment(tmp_path)
    assert "none (no size in the headers and none stated: uncalibrated" in _text(
        nb.pixel_line.value
    )
    assert ui.is_visible(nb.pixel_form) and not ui.is_visible(nb.pixel_change)
    out = tmp_path / "out"
    assert not (out / "calibration.csv").exists()  # nothing is stated for the user
    nb.pixel_size.value = "0.108"
    nb.pixel_apply.click()
    text = (out / "calibration.csv").read_text(encoding="utf-8")
    assert ".,X,0.108,micrometer" in text and ".,Y,0.108,micrometer" in text
    assert _text(nb.pixel_line.value) == (
        "Pixel size: 0.108 × 0.108 µm (stated by you, same in all 2 images)."
    )
    sizes = nb.preflight.calibration.sizes
    assert {(s.role, s.value, s.source) for s in sizes["ctrl/rep1/a.tif"] if s.role in "XY"} == {
        ("X", 0.108, "stated"),
        ("Y", 0.108, "stated"),
    }


def test_the_pixel_size_is_two_labelled_inputs_x_and_y_with_same_for_both(tmp_path):
    """Owner's test of v0.5.1: "One pixel is ..." was unclear."""
    nb = experiment(tmp_path)
    labels = _text(" ".join(w.value for w in nb.pixel_form.children if type(w).__name__ == "HTML"))
    assert "One pixel is" not in labels and "Pixel size&nbsp; X" in labels and "Y" in labels
    assert nb.pixel_same.value and nb.pixel_same.description == "same for X and Y"
    nb.pixel_x.value = "0.108"
    assert nb.pixel_y.value == "0.108" and nb.pixel_y.disabled  # Y follows X while ticked
    nb.pixel_same.value = False
    assert not nb.pixel_y.disabled
    nb.pixel_y.value = "0.11"
    nb.pixel_apply.click()
    text = (tmp_path / "out" / "calibration.csv").read_text(encoding="utf-8")
    assert ".,X,0.108,micrometer" in text and ".,Y,0.11,micrometer" in text
    assert "0.108 × 0.11 µm (stated by you" in _text(nb.pixel_line.value)
    nb.pixel_same.value = True  # ticked again: Y is X again, and only then stated
    assert nb.pixel_y.value == "0.108"


def test_a_wrong_size_changes_nothing_and_says_why(tmp_path):
    nb = experiment(tmp_path)
    for wrong in ("", "abc", "-1", "0"):
        nb.pixel_size.value = wrong
        nb.pixel_apply.click()
        assert "Nothing was changed" in nb.calibration_message.value, wrong
    assert not (tmp_path / "out" / "calibration.csv").exists()
    nb.pixel_unit.value = "pixels"
    assert nb.pixel_size.disabled
    nb.pixel_apply.click()
    assert "pixels (stated by you as deliberately uncalibrated" in _text(nb.pixel_line.value)


def test_headers_that_disagree_open_the_units_and_nothing_is_chosen(tmp_path):
    nb = experiment(tmp_path, {"ctrl/rep1": 0.108, "drug/rep1": 0.216})
    line = _text(nb.pixel_line.value)
    assert (
        line == "Headers disagree: 0.108 × 0.108 µm in 1 image, 0.216 × 0.216 µm in 1 (drug/rep1)."
    )
    assert "mb-warn" in nb.pixel_line.value
    assert nb.pixel_units.selected_index == 0  # the per-unit part is shown, not hidden
    table = _text(nb.unit_table.value)
    assert "ctrl/rep1 0.108 × 0.108 µm from the headers" in table
    assert "drug/rep1 0.216 × 0.216 µm from the headers" in table
    assert not (tmp_path / "out" / "calibration.csv").exists()
    recorded = {
        path: {s.role: (s.value, s.source) for s in sizes}
        for path, sizes in nb.preflight.calibration.sizes.items()
    }
    assert recorded["ctrl/rep1/a.tif"]["X"] == (0.108, "header")
    assert recorded["drug/rep1/a.tif"]["X"] == (0.216, "header")  # each as its header says


def test_a_size_per_unit_and_then_one_for_all_are_each_the_users_statement(tmp_path):
    nb = experiment(tmp_path, {"ctrl/rep1": 0.108, "drug/rep1": 0.216})
    out = tmp_path / "out"
    nb.calibration_target.value = "drug/rep1"
    nb.calibration_size.value = "0.108"
    nb.calibration_state.click()
    assert "drug/rep1,X,0.108,micrometer" in (out / "calibration.csv").read_text(encoding="utf-8")
    assert "Pixel sizes differ" in _text(nb.pixel_line.value)  # header and stated: still two
    assert "calibration differs from header" in _text(nb.calibration_issues.value)
    nb.calibration_axes.value = "Z"
    nb.calibration_target.value = "."
    nb.calibration_size.value = "2"
    nb.calibration_state.click()
    nb.pixel_size.value = "0.5"
    nb.pixel_apply.click()  # one value for all: explicit, and it replaces the per-unit X and Y
    assert "drug/rep1: X 0.108 micrometer" in _text(nb.pixel_replace_text.value)  # shown first
    nb.pixel_replace_yes.click()
    text = (out / "calibration.csv").read_text(encoding="utf-8")
    assert ".,X,0.5,micrometer" in text and ".,Y,0.5,micrometer" in text
    assert "drug/rep1" not in text and ".,Z,2.0,micrometer" in text
    assert _text(nb.pixel_line.value) == (
        "Pixel size: 0.5 × 0.5 µm (stated by you, same in all 2 images)."
    )
    assert "calibration differs from header" in _text(nb.calibration_issues.value)  # still said


def test_one_size_for_all_shows_what_it_replaces_before_it_takes_effect(tmp_path):
    nb = experiment(tmp_path, {"ctrl/rep1": 0.108, "drug/rep1": 0.216})
    out = tmp_path / "out"
    nb.calibration_target.value = "drug/rep1"
    nb.calibration_size.value = "0.108"
    nb.calibration_state.click()
    before = (out / "calibration.csv").read_bytes()
    nb.pixel_size.value = "0.5"
    nb.pixel_apply.click()
    assert (out / "calibration.csv").read_bytes() == before  # nothing yet: the user must see
    assert ui.is_visible(nb.pixel_replace_box)
    shown = _text(nb.pixel_replace_text.value)
    assert "drug/rep1: X 0.108 micrometer" in shown and "drug/rep1: Y 0.108 micrometer" in shown
    assert "The images have now: Pixel sizes differ: 0.108 × 0.108 µm in 1 image" in shown
    nb.pixel_replace_no.click()
    assert not ui.is_visible(nb.pixel_replace_box)
    assert (out / "calibration.csv").read_bytes() == before
    nb.pixel_apply.click()
    nb.pixel_replace_yes.click()
    text = (out / "calibration.csv").read_text(encoding="utf-8")
    assert ".,X,0.5,micrometer" in text and "drug/rep1" not in text
    assert "Replaced: drug/rep1: X 0.108 micrometer" in _text(nb.calibration_message.value)


def test_one_size_for_all_refuses_when_the_statements_changed_after_they_were_shown(tmp_path):
    nb = experiment(tmp_path, {"ctrl/rep1": 0.108, "drug/rep1": 0.216})
    out = tmp_path / "out"
    nb.pixel_size.value = "0.5"
    nb.pixel_apply.click()  # replaces the header sizes: shown first
    assert ui.is_visible(nb.pixel_replace_box)
    out.mkdir(exist_ok=True)
    (out / "calibration.csv").write_text(
        "target,axis,size,unit\nctrl/rep1,X,0.3,micrometer\n", encoding="utf-8"
    )
    nb.pixel_replace_yes.click()
    assert "Nothing was changed" in nb.calibration_message.value
    assert "ctrl/rep1,X,0.3" in (out / "calibration.csv").read_text(encoding="utf-8")


def test_one_size_for_all_with_nothing_to_replace_takes_effect_at_once(tmp_path):
    nb = experiment(tmp_path)  # no size in the headers, nothing stated
    nb.pixel_size.value = "0.2"
    nb.pixel_apply.click()
    assert not ui.is_visible(nb.pixel_replace_box)
    assert ".,X,0.2,micrometer" in (tmp_path / "out" / "calibration.csv").read_text("utf-8")
