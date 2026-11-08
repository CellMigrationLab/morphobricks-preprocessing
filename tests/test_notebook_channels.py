"""The *Your channels* screen: labels first, then tags and image type per unit (Stage 1, PR 15).

Widgets are driven headless, as in ``test_notebook_description``. Nothing is saved or confirmed
until the user answers; every answer is in ``morphobricks.draft.json``.
"""

import shutil

import pytest

from channel_images import write_channels, write_rgb
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from morphobricks_preprocessing.draft_format import answers_from_bytes

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
KINDS = ["blobs", "filaments", "cells"]
DNA, ACTIN, PHASE = "DNA", "ACTIN", "PHASE"


def tree(base, names_of, folders=FOLDERS):
    """Write one named-channel image per folder; ``names_of(folder)`` gives its header names."""
    for i, folder in enumerate(folders):
        write_channels(base / "exp" / folder / "a.tif", names_of(folder), KINDS, seed=i + 1)
    return base / "exp"


def open_screen(root, out):
    """Open the notebook on ``root``, check the folder and confirm the axis roles."""
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.channels_step, nb.run_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None
    return nb


def set_labels(nb, labels):
    """Type the labels into the slots (adding slots as needed) and confirm the list."""
    card = nb.channel_labels
    while len(card.rows) < len(labels):
        card.add_button.click()
    for row, text in zip(card.rows, labels + [""] * len(card.rows), strict=False):
        row.id_box.value = text
    card.confirm.button.click()
    assert card.confirmed


def saved(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes())


def tag_all(nb, tags=(DNA, ACTIN, PHASE), *, skip=()):
    """Tag every cell of every unit (not those of ``skip``) and answer every image type."""
    grid = nb.channel_grid
    for key, index in list(grid.cells):
        if key not in skip:
            grid.cells[(key, index)].value = tags[index]
    for layout in grid.layouts:
        if layout.key not in skip:
            nb.channel_types.radios[layout.key].value = "channels"


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


@pytest.fixture
def plain(tmp_path):
    """Four units whose headers say DAPI, Actin and Ch3: nothing a label could be."""
    return tree(tmp_path, lambda f: ["DAPI", "Actin", "Ch3"])


# --- step A: the label list ----------------------------------------------------------------------


def test_generic_header_names_are_not_suggested_as_labels(plain, out):
    nb = open_screen(plain, out)
    card = nb.channel_labels
    assert [r.id_box.value for r in card.rows] == ["Actin", "", ""]  # one slot per channel
    assert "DAPI" in card.skipped.value and "Ch3" in card.skipped.value
    assert not any(r.id_box.value in {"DAPI", "Ch3"} for r in card.rows)
    assert not card.confirmed and not (out / DRAFT_FILENAME).exists()
    assert not ui.is_visible(nb.channel_grid.widget)  # step B waits for the labels


def test_the_slots_are_the_channel_count_of_the_images(tmp_path, out):
    root = tmp_path / "exp"
    for folder in FOLDERS:
        write_channels(root / folder / "a.tif", ["Actin", "Tubulin"], KINDS[:2])
    nb = open_screen(root, out)
    assert nb.channel_labels.slots == 2 and len(nb.channel_labels.rows) == 2


def test_near_identical_spellings_are_grouped_and_never_merged(tmp_path, out):
    root = tree(tmp_path, lambda f: ["Lamin", "LaminB", "Actin"])
    nb = open_screen(root, out)
    card = nb.channel_labels
    assert card.groups == [("Lamin", "LaminB")]
    assert [r.id_box.value for r in card.rows] == ["Actin", "", ""]  # neither is a label yet
    assert "look alike" in card.groups_box.children[0].children[0].value
    assert "Lamin" in card.groups_box.children[0].children[0].value
    assert not (out / DRAFT_FILENAME).exists()
    # the user decides: "Same label" puts the chosen spelling in a slot, and only that one
    _, buttons = card.groups_box.children[0].children
    pick, same, different, _ = buttons.children
    pick.value = "LaminB"
    same.click()
    assert [r.id_box.value for r in card.rows] == ["Actin", "LaminB", ""]
    assert card.groups == []


def test_different_labels_puts_each_spelling_in_a_slot(tmp_path, out):
    root = tree(tmp_path, lambda f: ["Lamin", "LaminB", "Actin"])
    card = open_screen(root, out).channel_labels
    card.groups_box.children[0].children[1].children[2].click()
    assert sorted(r.id_box.value for r in card.rows) == ["Actin", "Lamin", "LaminB"]


def test_spellings_that_differ_only_by_case_cannot_be_different_labels(tmp_path, out):
    root = tree(tmp_path, lambda f: ["Actin", "ACTIN", "Golgi"])
    card = open_screen(root, out).channel_labels
    assert card.groups == [("ACTIN", "Actin")]
    different = card.groups_box.children[0].children[1].children[2]
    assert different.disabled
    assert "E_ID_CASE_COLLISION" in card.groups_box.children[0].children[0].value


def test_a_case_collision_is_refused_at_once_and_nothing_is_saved(plain, out):
    nb = open_screen(plain, out)
    card = nb.channel_labels
    card.rows[0].id_box.value = "Actin"
    card.rows[1].id_box.value = "ACTIN"
    assert "E_ID_CASE_COLLISION" in card.rows[0].state.value
    assert "E_ID_CASE_COLLISION" in card.rows[1].state.value
    assert card.confirm.disabled
    card.confirm.button.click()
    assert not card.confirmed and not (out / DRAFT_FILENAME).exists()


def test_add_label_adds_a_slot(plain, out):
    nb = open_screen(plain, out)
    card = nb.channel_labels
    before = len(card.rows)
    card.add_button.click()
    assert len(card.rows) == before + 1
    set_labels(nb, ["DNA", "ACTIN", "PHASE", "LAMIN"])
    assert sorted(saved(out).channels) == ["ACTIN", "DNA", "LAMIN", "PHASE"]


def test_the_label_list_is_saved_with_shown_as_names_only_on_confirmation(plain, out):
    nb = open_screen(plain, out)
    card = nb.channel_labels
    card.rows[0].id_box.value, card.rows[0].name_box.value = DNA, "DAPI"
    assert not (out / DRAFT_FILENAME).exists()
    set_labels(nb, [DNA, ACTIN, PHASE])
    assert saved(out).channels[DNA].display_name == "DAPI"


# --- step B: the tag grid ------------------------------------------------------------------------


def test_each_dropdown_offers_the_labels_and_not_imaged_only(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    assert ui.is_visible(grid.widget)
    assert len(grid.cells) == 4 * 3  # one dropdown per unit and C index
    for drop in grid.cells.values():
        assert [value for _, value in drop.options] == [None, ACTIN, DNA, PHASE, ""]
    assert all(drop.value is None for drop in grid.cells.values())  # nothing chosen for you


def test_each_unit_has_a_thumbnail_a_c_index_and_its_quoted_header_name(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    text = repr(nb.channel_grid.blocks.children)
    assert "‘DAPI’" in text or "&lsquo;DAPI&rsquo;" in text
    assert "first image by path: ctrl/rep1/a.tif" in text
    pictures = [w for w in _all(nb.channel_grid.blocks) if w.__class__.__name__ == "Image"]
    assert len(pictures) == 12 and all(p.value[:4] == b"\x89PNG" for p in pictures)


def _all(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from _all(child)


def test_incomplete_tagging_blocks_completion(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    assert not grid.complete and "Not finished" in grid.status.value
    tag_all(nb, skip=[("drug", "rep2", "single")])
    assert not grid.complete
    assert "3 channel(s) still to tag" in grid.status.value
    assert "1 image type(s) still to choose" in grid.status.value
    tag_all(nb)
    assert grid.complete and "Every channel of every one of the 4 units" in grid.status.value


def test_the_dropdown_has_no_empty_option_and_an_unused_slot_needs_a_label(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, "unused"])
    grid = nb.channel_grid
    for drop in grid.cells.values():
        assert [label for _, label in drop.options] == [None, DNA, ACTIN, "unused"]
    assert "unused" in nb.channel_labels.widget.children[1].value  # the Step A hint
    tag_all(nb, (DNA, ACTIN, "unused"))
    assert grid.complete
    assert all(u.channels == (DNA, ACTIN, "unused") for u in saved(out).units.values())


def test_the_step_b_is_incomplete_while_one_position_has_no_label(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, "unused"])
    tag_all(nb, (DNA, ACTIN, "unused"), skip=[("drug", "rep2", "single")])
    assert not nb.channel_grid.complete


def test_a_chip_shows_the_id_and_the_shown_as_name_only_as_tooltip(plain, out):
    nb = open_screen(plain, out)
    card = nb.channel_labels
    while len(card.rows) < 3:
        card.add_button.click()
    for row, text in zip(card.rows, [DNA, ACTIN, "unused"], strict=True):
        row.id_box.value = text
    card.rows[0].name_box.value = "DAPI stain"
    assert ">DNA</span>" in card.rows[0].state.value
    assert "DAPI stain</span>" not in card.rows[0].state.value
    assert "title='DAPI stain'" in card.rows[0].state.value
    card.confirm.button.click()
    tag_all(nb, (DNA, ACTIN, "unused"))
    html_texts = [w.value for w in _all(nb.channel_grid.widget) if hasattr(w, "value")]
    assert any(">DNA</span>" in v and "title='DAPI stain'" in v for v in html_texts if isinstance(v, str))
    assert not any("DAPI stain</span>" in v for v in html_texts if isinstance(v, str))


def test_a_label_cannot_stand_at_two_positions_of_a_unit(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    key = ("ctrl", "rep1", "single")
    grid.cells[(key, 0)].value = DNA
    grid.cells[(key, 1)].value = DNA
    assert "one label cannot stand at two positions" in grid.problem.value
    assert saved(out).units[key].channels == (DNA, None, None)


def test_a_label_used_in_only_some_units_says_what_will_be_refused(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    tag_all(nb, skip=[("drug", "rep2", "single")])
    text = grid.usage_html.value
    assert "used in 3 of 4 units: an analysis of DNA will be refused for the other units" in text
    assert "A_SCOPE_INCOMPLETE" in text
    tag_all(nb)
    assert "used in all 4 units" in grid.usage_html.value
    assert "will be refused" not in grid.usage_html.value


def test_a_label_in_another_column_in_one_unit_is_pointed_out(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    tag_all(nb, skip=[("drug", "rep2", "single")])
    key = ("drug", "rep2", "single")
    grid = nb.channel_grid
    for index, tag in enumerate((ACTIN, DNA, PHASE)):
        grid.cells[(key, index)].value = tag
    assert "DNA is not always in the same column" in grid.usage_html.value


def test_a_pre_tag_from_the_header_is_only_a_suggestion_until_confirmed(tmp_path, out):
    root = tree(tmp_path, lambda f: ["DNA", "ACTIN", "PHASE"])
    nb = open_screen(root, out)
    assert [r.id_box.value for r in nb.channel_labels.rows] == ["DNA", "ACTIN", "PHASE"]
    set_labels(nb, ["ACTIN", "DNA", "PHASE"])
    grid = nb.channel_grid
    assert {k[1]: d.value for k, d in grid.cells.items() if k[0] == ("ctrl", "rep1", "single")} == {
        0: DNA,
        1: ACTIN,
        2: PHASE,
    }
    assert "proposed, not confirmed" in repr(grid.blocks.children)
    assert not saved(out).units  # nothing is the user's answer yet
    assert not grid.complete
    assert len(grid.confirm_buttons) == 1  # one confirm for the one layout
    assert "4 unit(s)" in grid.confirm_buttons[0].description
    grid.confirm_buttons[0].click()
    assert all(u.channels == (DNA, ACTIN, PHASE) for u in saved(out).units.values())
    assert "proposed, not confirmed" not in repr(grid.blocks.children)
    assert not grid.confirm_buttons
    assert not grid.complete  # the image type is still to answer


def test_units_with_different_header_names_are_different_layouts(tmp_path, out):
    root = tree(
        tmp_path, lambda f: ["DNA", "ACTIN", "PHASE"] if "ctrl" in f else ["DNA", "ACTIN", "x"]
    )
    nb = open_screen(root, out)
    set_labels(nb, ["ACTIN", "DNA", "PHASE"])
    grid = nb.channel_grid
    assert len(grid.confirm_buttons) == 1  # only the layout that equals the labels exactly
    assert "2 unit(s)" in grid.confirm_buttons[0].description
    assert grid.cells[(("drug", "rep1", "single"), 0)].value is None


# --- the image type ------------------------------------------------------------------------------


def test_the_image_type_is_not_preselected(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    radios = nb.channel_types.radios
    assert len(radios) == 4 and all(r.value is None for r in radios.values())
    assert [value for _, value in next(iter(radios.values())).options] == ["channels", "rgb"]
    assert not (saved(out).units)


def rgb_tree(base):
    for i, folder in enumerate(FOLDERS):
        write_rgb(base / "exp" / folder / "a.tif", seed=i + 1)
    return base / "exp"


def test_colour_is_pre_ticked_only_when_the_header_says_rgb(tmp_path, out):
    nb = open_screen(rgb_tree(tmp_path), out)
    assert nb.channel_labels.slots == 1  # colour images have no stain slots
    set_labels(nb, ["DNA"])
    radios = nb.channel_types.radios
    assert len(radios) == 4 and all(r.value == "rgb" for r in radios.values())
    key = ("drug", "rep2", "single")
    assert "Not saved until you confirm" in nb.channel_types.notes[key].value
    assert not saved(out).units  # the pre-tick is not an answer
    assert not nb.channel_grid.complete


def test_confirming_the_colour_layout_declares_r_g_b_and_completes_every_unit(tmp_path, out):
    nb = open_screen(rgb_tree(tmp_path), out)
    set_labels(nb, ["DNA"])
    grid = nb.channel_grid
    assert len(grid.confirm_buttons) == 1 and "4 unit(s)" in grid.confirm_buttons[0].description
    grid.confirm_buttons[0].click()
    answers = saved(out)
    assert all(
        (u.channels, u.image_type) == (("R", "G", "B"), "rgb") for u in answers.units.values()
    )
    assert {"R", "G", "B", "DNA"} == set(answers.channels)
    assert all(answers.channels[t].header_names is None for t in "RGB")
    assert not grid.cells  # a colour unit has no dropdown: its channels are R, G, B
    assert grid.complete


def test_a_type_that_contradicts_the_header_is_allowed_and_said(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    key = ("ctrl", "rep1", "single")
    nb.channel_types.radios[key].value = "rgb"  # three channels: allowed, the run will refuse
    assert "header does not say colour" in nb.channel_types.notes[key].value
    assert saved(out).units[key].image_type == "rgb"


# --- saving and resuming ---------------------------------------------------------------------


def test_every_answer_is_saved_and_a_reopened_notebook_shows_it(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    tag_all(nb)
    answers = saved(out)
    assert {c: a.header_names for c, a in answers.channels.items()} == {
        ACTIN: ("Actin",),
        DNA: ("DAPI",),
        PHASE: ("Ch3",),
    }  # the tag is the confirmation of each header name as evidence
    again = open_screen(plain, out)
    card, grid = again.channel_labels, again.channel_grid
    assert card.confirmed and card.labels == [ACTIN, DNA, PHASE]
    assert ui.is_visible(grid.widget) and grid.complete
    assert all(d.value == (DNA, ACTIN, PHASE)[k[1]] for k, d in grid.cells.items())
    assert all(r.value == "channels" for r in again.channel_types.radios.values())
    assert "Every channel of every one of the 4 units" in grid.status.value


def test_changing_the_labels_takes_back_the_tags_of_a_label_that_is_gone(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    tag_all(nb)
    card = nb.channel_labels
    card.confirm.button.click()  # change the labels
    assert not card.confirmed and not ui.is_visible(nb.channel_grid.widget)
    ids = [r.id_box.value for r in card.rows]
    card.rows[ids.index(PHASE)].id_box.value = "GOLGI"
    card.confirm.button.click()
    assert "tag(s) of a label that is no longer in the list were taken back" in card.status.value
    assert all(u.channels == (DNA, ACTIN, None) for u in saved(out).units.values())
    assert not nb.channel_grid.complete


def test_the_other_cards_answers_are_not_touched_by_the_channel_answers(plain, out):
    nb = open_screen(plain, out)
    nb.id_inputs[("conditions", "ctrl")].value = "ctrl"
    nb.ids_confirmed.button.click()
    before = saved(out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    after = saved(out)
    assert after.conditions == before.conditions and after.repeats == before.repeats


def test_a_removed_unit_drops_only_its_own_answer(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    tag_all(nb)
    assert len(saved(out).units) == 4
    shutil.rmtree(plain / "drug" / "rep2")
    again = open_screen(plain, out)
    assert "drug / rep2 / single" in again.names_status.value
    grid = again.channel_grid
    assert grid.complete and len(grid.layouts) == 3
    assert all(d.value == (DNA, ACTIN, PHASE)[k[1]] for k, d in grid.cells.items())
    assert len(saved(out).units) == 4  # the file is read, not rewritten, until the next answer
    grid.cells[(("ctrl", "rep1", "single"), 2)].value = ""  # the next answer saves what remains
    assert set(saved(out).units) == {
        ("ctrl", "rep1", "single"),
        ("ctrl", "rep2", "single"),
        ("drug", "rep1", "single"),
    }


def test_a_unit_whose_channel_count_changed_is_tagged_again_not_stretched(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    tag_all(nb)
    for folder in FOLDERS:
        write_channels(plain / folder / "a.tif", ["DAPI", "Actin"], KINDS[:2])
    again = open_screen(plain, out)
    grid = again.channel_grid
    assert sorted({k[1] for k in grid.cells}) == [0, 1]
    assert all(d.value is None for d in grid.cells.values())
    assert not grid.complete


# --- the screen waits for what it needs ---------------------------------------------------------


def test_the_screen_waits_for_the_axis_roles(plain, out):
    nb = PreprocessingNotebook(str(plain), output_folder=str(out))
    nb.channels_step()
    assert not ui.is_visible(nb.channel_labels.widget)
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    assert "Waiting for the axis roles" in nb.channels_waiting.value
    assert not ui.is_visible(nb.channel_labels.widget)


def test_the_enlarged_view_shows_the_1024_px_picture(plain, out):
    nb = open_screen(plain, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    zoom = next(
        w
        for w in _all(grid.blocks)
        if w.__class__.__name__ == "Button" and "Enlarge" in w.description
    )
    zoom.click()
    assert len(grid.detail.children) == 3
    assert grid.detail.children[1].value[:4] == b"\x89PNG"
