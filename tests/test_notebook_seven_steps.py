"""The 7-step flow of the shipped notebook (owner, 10 November 2026, items (o), (p) and (q)).

- the image type is asked with the axes, in one line pre-filled from the image headers and
  recorded only by a click; each folder on demand; folders that differ are said at once;
- the conditions, the replicates and "compared against" are one grid with one confirmation;
- the mask step (*Describe the masks*) is on the Masks step and waits, visibly, for a run.

The proposals are never answers: nothing is saved before the click. For the same answers the
stepper publishes the bytes the stacked pages publish.
"""

import re

import numpy as np
import pytest
import tifffile

from channel_images import write_channels, write_rgb
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.design import DESIGN_FILENAME
from test_notebook_check_save import add_folder, answer_all, exp, opened, tag  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.channel_labels import UnitLayout  # noqa: E402
from morphobricks_preprocessing.draft_answers import DraftAnswers  # noqa: E402
from morphobricks_preprocessing.notebook_channels_type import (  # noqa: E402
    ImageTypeColumn,
    ImageTypeRows,
)
from morphobricks_preprocessing.notebook_check_view import TODO_SHOWN, todo_html  # noqa: E402
from morphobricks_preprocessing.notebook_design_step import NOT_CONFIRMED  # noqa: E402
from morphobricks_preprocessing.notebook_image_type_line import (  # noqa: E402
    MIXED,
    WAITING,
    ImageTypeLine,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _types(nb):
    return {key: unit.image_type for key, unit in nb.draft_answers.units.items()}


def _colour(tmp_path, colour=FOLDERS, *, confirm=True):
    """Return a stepper on four folders, those of ``colour`` holding colour (RGB) pictures."""
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        if folder in colour:
            write_rgb(root / folder / "a.tif", seed=i + 1)
        else:
            write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], ["blobs"] * 3, seed=i)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    if confirm:
        nb.quick_confirm.click()
    return nb


# --- (p) the image type, with the axes ----------------------------------------------------------


def test_the_image_type_waits_for_the_axis_roles_and_says_so(exp):  # noqa: F811
    nb = stepper(*exp)
    line = nb.type_line
    assert _text(line.line.value) == WAITING
    assert not ui.is_visible(line.form) and not ui.is_visible(line.per_folder)
    nb.quick_confirm.click()
    assert ui.is_visible(line.form) and ui.is_visible(line.per_folder)
    assert line.per_folder.selected_index is None  # each folder on demand


def test_the_header_s_type_is_a_proposal_saved_only_by_the_click(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    line = nb.type_line
    assert line.choice.value == "channels" and line.suggestion() == "channels"
    said = _text(line.line.value)
    assert "Proposed from the image headers: separate channels in all 4 folders" in said
    assert "Not saved until you click Use for all folders" in said
    assert set(_types(nb).values()) <= {None} and not line.complete  # nothing was applied
    assert not nb._stepper.states()[1].done
    assert ui.is_visible(line.apply) and not line.apply.disabled
    line.apply.click()
    assert set(_types(nb).values()) == {"channels"} and len(_types(nb)) == 4 and line.complete
    assert "Image type: separate channels in all 4 folders." in _text(line.line.value)
    assert not ui.is_visible(line.apply)  # nothing left to record
    assert nb._stepper.states()[1].done


def test_one_click_for_all_folders_records_what_each_folder_s_choice_records(exp, tmp_path):  # noqa: F811
    root, masks, out = exp
    one = stepper(root, masks, out)
    one.quick_confirm.click()
    one.type_line.apply.click()
    each = stepper(root, masks, tmp_path / "each")
    each.quick_confirm.click()
    for radio in list(each.channel_types.radios.values()):
        radio.value = "channels"
    assert one.draft_answers.units == each.draft_answers.units
    assert one.draft_answers.channels == each.draft_answers.channels


def test_headers_that_all_say_colour_propose_colour_and_the_click_confirms_it(tmp_path):
    nb = _colour(tmp_path)
    line = nb.type_line
    assert line.choice.value == "rgb" and "colour (RGB) in all 4 folders" in _text(line.line.value)
    assert not nb._stains_not_applicable and set(_types(nb).values()) <= {None}
    line.apply.click()
    assert set(_types(nb).values()) == {"rgb"} and nb._stains_not_applicable
    assert all(unit.channels == ("R", "G", "B") for unit in nb.draft_answers.units.values())


class _Store:
    """The answers of a card, kept in memory (the cards' :class:`AnswerStore`)."""

    def __init__(self):
        self.saved = DraftAnswers("0" * 64, "spec")

    def answers(self):
        return self.saved

    def update(self, new):
        self.saved = new
        return True

    def refusal(self):
        return ""

    def used_channels(self):
        return ()


def _line(colour):
    """Return the image-type line over four units, those of ``colour`` with a colour header."""
    layouts = tuple(
        UnitLayout((c, r, "single"), "a.tif", 3, (None,) * 3, rgb=(c, r) in colour)
        for c in ("ctrl", "drug")
        for r in ("rep1", "rep2")
    )
    types = ImageTypeColumn(_Store(), lambda: line.rows.refresh() or line.refresh())
    line = ImageTypeLine(types, ImageTypeRows(types))
    line.rows.open(layouts)
    line.refresh()
    return line


def test_headers_that_differ_propose_nothing_and_open_each_folder():
    line = _line({("drug", "rep2")})
    assert line.suggestion() is None and line.choice.value is None
    said = _text(line.line.value)
    assert "The image headers differ: separate channels in 3 folders, colour (RGB) in 1" in said
    assert "Nothing is proposed" in said and "R_HETEROGENEOUS_LAYOUT_NOT_ENABLED" in said
    assert line.per_folder.selected_index == 0  # the menu is open
    assert line.apply.disabled and "choose a type in the box" in _text(line.blocker.value)
    assert not line.types.store.saved.units  # nothing was applied


def test_folders_answered_differently_are_said_at_once_with_the_refusal_to_come():
    line = _line({("drug", "rep2")})
    for layout in line.types.layouts:
        if not layout.rgb:
            line.types.radios[layout.key].value = "channels"
    line.types.confirm_header([u.key for u in line.types.layouts if u.rgb])  # the pre-tick
    said = _text(line.line.value)
    assert "Image types differ: separate channels in 3 folders, colour (RGB) in 1" in said
    assert MIXED in said and line.complete
    one = _line(set())
    key = one.types.layouts[0].key
    one.types.radios[key].value = "channels"
    assert "Not finished: separate channels in 1 folder, 3 not answered" in _text(one.line.value)


def test_colour_pictures_beside_separate_channels_stay_refused_at_the_axes(tmp_path):
    nb = _colour(tmp_path, colour=["drug/rep2"], confirm=False)
    assert nb.mapping is None and nb.confirmed_mapping is None  # as before: no roles to confirm
    assert "No axis roles can be suggested" in nb.axis_box.children[0].value
    assert _text(nb.type_line.line.value) == WAITING and not ui.is_visible(nb.type_line.form)
    assert nb.run_button.disabled and not nb._stepper.states()[1].done


def test_replacing_answers_already_given_asks_first(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    line = nb.type_line
    line.apply.click()
    before = nb.draft_answers
    line.choice.value = "rgb"  # three channels: colour can be declared (the run checks headers)
    assert ui.is_visible(line.apply)
    line.apply.click()
    assert ui.is_visible(line.replace_box) and nb.draft_answers == before  # nothing written yet
    assert "4 folders answered otherwise" in _text(line.replace_text.value)
    line.replace_no.click()
    assert not ui.is_visible(line.replace_box) and nb.draft_answers == before
    line.apply.click()
    line.replace_yes.click()
    assert set(_types(nb).values()) == {"rgb"}
    # The per-folder rows say what the run will do with a type the header contradicts.
    assert "The header does not say colour" in repr(nb.type_rows.rows.children)


def test_a_type_the_images_cannot_have_changes_nothing(tmp_path):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        (root / folder).mkdir(parents=True)
        data = np.zeros((2, 16, 16), np.uint8)
        tifffile.imwrite(root / folder / "a.tif", data, metadata={"axes": "CYX"})
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    line = nb.type_line
    line.choice.value = "rgb"
    line.apply.click()
    assert "Nothing was changed" in line.message.value
    assert "a colour image has 3 channels" in line.message.value
    assert set(_types(nb).values()) <= {None}


def test_the_channels_step_waits_for_the_image_type_and_says_where(exp):  # noqa: F811
    nb = stepper(*exp)
    nb._stepper.go(2)
    assert "Waiting for the axis roles" in nb._stepper.waits[2].value
    nb.quick_confirm.click()
    nb._stepper.go(2)
    assert "Waiting for the image type" in nb._stepper.waits[2].value
    assert "Axes (step 2)" in nb._stepper.waits[2].value
    nb.type_line.apply.click()
    nb._stepper.go(2)
    assert nb._stepper.waits[2].value == ""


# --- (q) the design: one grid, one click --------------------------------------------------------


def test_the_design_is_proposed_and_nothing_is_saved_before_the_click(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    assert ui.is_visible(nb.design_step_card) and ui.is_visible(nb.design_confirm)
    cells = nb._design_cells
    assert "<b>ctrl</b>" in cells[("condition", "ctrl")].value
    assert "proposed" in cells[("condition", "ctrl")].value
    assert "<b>rep1</b>" in cells[("repeat", "rep1")].value
    assert _text(cells[("drug", "rep2")].value) == "rep2 1 image"
    assert [box.value for box in nb.reference_boxes.values()] == ["", ""]  # no comparison
    assert all(None not in [v for _, v in box.options] for box in nb.reference_boxes.values())
    # A proposal is not an answer: no ID is adopted and no comparison is saved.
    assert not nb.draft_answers.conditions and not nb._ids_all_confirmed()
    assert not nb._design_confirmed() and not nb._stepper.states()[4].done
    assert "not confirmed" in nb._stepper.states()[4].text


def test_the_save_waits_for_the_design_s_click_and_says_so(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    tag(nb)
    nb.mask_card.questions["Nuclei"].choice.value = "instances"
    for toggle in list(nb.same_way.values()):
        toggle.button.click()
    assert nb.save_button.disabled and not nb.save_experiment()
    assert NOT_CONFIRMED in _text(nb.todo.value)
    assert "is compared against (choose" not in nb.todo.value  # the one click answers them
    assert "1 answer still missing" in _text(nb.save_blocker.value)
    nb.design_confirm.click()
    assert nb.todo.value == "" and not nb.save_button.disabled
    assert nb.save_experiment()


def test_the_click_adopts_every_name_and_no_comparison_as_shown(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    nb.reference_boxes["drug"].value = "ctrl"  # a choice in the grid is saved at once
    assert nb.draft_answers.conditions["drug"].reference == "ctrl"
    table, boxes = nb.design_table.children, dict(nb.reference_boxes)
    nb.design_confirm.click()
    answers = nb.draft_answers
    assert answers.conditions["ctrl"].reference == "" and answers.conditions["ctrl"].id == "ctrl"
    assert (
        answers.conditions["drug"].reference == "ctrl" and answers.conditions["drug"].id == "drug"
    )
    assert {k: v.id for k, v in answers.repeats.items()} == {"rep1": "rep1", "rep2": "rep2"}
    assert nb._design_confirmed() and nb._stepper.states()[4].done
    assert not ui.is_visible(nb.design_confirm) and ui.is_visible(nb.design_change)
    assert "Design confirmed: 2 conditions × 2 biological repeats" in _text(nb.design_result.value)
    assert "proposed" not in nb._design_cells[("condition", "ctrl")].value
    # The grid is updated in place: the same cells and the same dropdowns.
    assert nb.design_table.children == table and dict(nb.reference_boxes) == boxes
    assert nb._stepper.states()[4].text.startswith("ctrl, drug → ctrl × rep1, rep2")
    nb.design_change.click()  # the names can be taken back to edit them
    assert not nb._design_confirmed() and ui.is_visible(nb.design_confirm)
    assert not nb.id_inputs[("conditions", "ctrl")].disabled


def test_a_folder_that_cannot_be_a_name_blocks_the_click_with_a_visible_reason(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(["NA/rep1", "NA/rep2", "drug/rep1", "drug/rep2"]):
        add_folder(root, masks, folder, i + 1)
    nb = stepper(root, masks, tmp_path / "out")
    nb.quick_confirm.click()
    assert nb.design_confirm.disabled
    assert "1 name missing (NA)" in _text(nb.design_blocker.value)
    assert "Edit names or pairing" in _text(nb.design_blocker.value)
    assert nb.design_edit.selected_index == 0  # opened: that is where the name is typed
    assert "needs a name" in nb._design_cells[("condition", "NA")].value
    nb.id_inputs[("conditions", "NA")].value = "untreated"
    assert not nb.design_confirm.disabled and nb.design_blocker.value == ""
    assert "<b>untreated</b>" in nb._design_cells[("condition", "NA")].value
    assert "folder NA" in nb._design_cells[("condition", "NA")].value
    nb.design_confirm.click()
    assert nb.draft_answers.conditions["NA"].id == "untreated" and nb._design_confirmed()


def test_a_refused_name_confirms_nothing_and_lists_the_refusal(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    nb.id_inputs[("conditions", "drug")].value = "ctrl"  # the ID of the other condition
    nb.design_confirm.click()
    assert "nothing was confirmed" in _text(nb.design_result.value)
    assert not nb._design_confirmed() and not nb.draft_answers.conditions
    assert nb.design_edit.selected_index == 0


def test_re_pairing_shows_in_the_grid_and_asks_for_the_click_again(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/day2"]):
        add_folder(root, masks, folder, i + 1)
    nb = stepper(root, masks, tmp_path / "out")
    nb.quick_confirm.click()
    assert "no folder" in nb._design_cells[("drug", "rep2")].value
    nb.design_confirm.click()
    assert nb._design_confirmed()
    (dropdown,) = [
        row.children[2]
        for row in nb.pairing_grid.children[1:]
        if "day2" in row.children[1].children[0].value
    ]
    dropdown.value = "rep2"
    assert ("drug", "day2") not in nb._design_cells
    assert _text(nb._design_cells[("drug", "rep2")].value) == "day2 1 image"
    assert nb.draft_answers.pairing == {("drug", "day2"): "rep2"}


def test_one_condition_has_no_comparison_column(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2"]):
        add_folder(root, masks, folder, i + 1)
    nb = stepper(root, masks, tmp_path / "out")
    nb.quick_confirm.click()
    assert "Compared against" not in repr(nb.design_table.children)
    nb.design_confirm.click()
    assert nb._design_confirmed() and not nb.draft_answers.conditions.get("ctrl").reference


def test_the_same_answers_publish_the_bytes_the_stacked_pages_publish(exp, tmp_path):  # noqa: F811
    root, masks, out = exp
    steps = stepper(root, masks, out)
    steps.quick_confirm.click()
    steps.type_line.apply.click()  # separate channels, for all folders
    answer_all(steps)  # drug against ctrl; the Design click records the rest as shown
    assert steps.save_experiment()
    pages = opened(root, masks, tmp_path / "pages")  # the pages other bricks embed
    answer_all(pages)  # the same answers, each given by hand
    assert pages.save_experiment()
    for name in (DESCRIPTION_FILENAME, DESIGN_FILENAME):
        assert (out / name).read_bytes() == (tmp_path / "pages" / name).read_bytes(), name


# --- (o) masks: what each is, then the mask step once a run recorded them ----------------------


def test_the_mask_step_is_on_masks_and_waits_visibly_for_a_run(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    assert nb.mask_step_button.disabled
    assert "Available after a run that recorded masks" in _text(nb.mask_step_hint.value)
    assert not ui.is_visible(nb.masks_hint)
    answer_all(nb)
    assert nb.save_experiment() and nb.run().written
    assert not nb.mask_step_button.disabled  # offered once the run recorded the masks
    assert ui.is_visible(nb.masks_hint) and "Masks (step 4)" in nb.masks_hint.value
    assert "relationships described" not in nb._stepper.states()[3].text
    assert nb._on_describe_masks() is not None
    assert "relationships described" in nb._stepper.states()[3].text


# --- shorter, never hidden ----------------------------------------------------------------------


def test_a_long_list_of_missing_answers_is_folded_and_complete():
    lines = [f"answer {i} <b>" for i in range(TODO_SHOWN + 5)]
    html = todo_html([], lines)
    shown, folded = html.split("<details>")
    assert f"Still to answer before saving: {len(lines)}." in shown
    assert shown.count("<li>answer ") == TODO_SHOWN - 2 and "and 7 more" in folded
    assert all(f"answer {i} &lt;b&gt;" in html for i in range(len(lines)))  # each one is there
    short = todo_html([], lines[:TODO_SHOWN])
    assert "<details>" not in short and short.count("<li>answer ") == TODO_SHOWN
