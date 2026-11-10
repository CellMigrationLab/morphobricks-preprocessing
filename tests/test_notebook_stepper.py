"""The shipped notebook's stepper: 13 steps, one on screen at a time (PR 17a, 2026-11-09).

Widgets are driven headless, as the user would: the strip's chips, Back and Next, and the
existing cards on each page. The stepper is pure composition, so the answers are those of the
cards; these tests pin the layout (order, one page shown, chip states, reopen landing) and that
moving between pages never changes or resets an answer.
"""

import numpy as np
import pytest
import tifffile

from channel_images import blobs
from test_notebook_check_save import TAGS, add_folder, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_stepper import (  # noqa: E402
    Step,
    Stepper,
    StepState,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

ORDER = [
    "Images found",
    "Axes",
    "Conditions",
    "Replicates",
    "Compared against",
    "Image type",
    "Stains",
    "Tag channels",
    "Masks",
    "Check and save",
    "Run",
    "Describe the masks",
]


def stepped(root, masks, out, *, axes=True):
    """Return a described notebook shown as the stepper, checked (and its axes confirmed)."""
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.welcome()
    nb.steps()
    nb.results_step()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    if axes:
        nb.preview_checked.set(True)
        nb.show_preview()
        nb.confirm_button.click()
    return nb


def kinds(nb):
    """Return each chip's colour class."""
    return [
        next(c.removeprefix("mb-chip-") for c in chip._dom_classes if c.startswith("mb-chip-"))
        for chip in nb._stepper.chips
    ]


def shown(nb):
    """Return the index of the one page on screen (and check there is exactly one)."""
    visible = [i for i, page in enumerate(nb._stepper.pages) if ui.is_visible(page)]
    assert len(visible) == 1
    return visible[0]


def snapshot(nb):
    """Return every answer and the value of every answering widget."""
    values = {
        "ids": {key: box.value for key, box in nb.id_inputs.items()},
        "names": {key: box.value for key, box in nb.name_inputs.items()},
        "references": {key: box.value for key, box in nb.reference_boxes.items()},
        "types": {key: radio.value for key, radio in nb.channel_types.radios.items()},
        "tags": {key: drop.value for key, drop in nb.channel_grid.cells.items()},
        "same_way": {key: toggle.value for key, toggle in nb.same_way.items()},
        "mask": nb.mask_card.questions["Nuclei"].choice.value,
        "pairing": [row.children[2].value for row in nb.pairing_grid.children[1:]],
    }
    return nb.draft_answers, nb.confirmed_mapping, values


# --- the generic stepper ------------------------------------------------------------------------


def _steps(done):
    return [
        Step(f"S{i}", "purpose", [], lambda i=i: StepState(i in done, f"line {i}"))
        for i in range(4)
    ]


def test_next_and_back_move_one_page_and_never_block():
    stepper = Stepper(_steps(done=set()))
    pages = lambda: [ui.is_visible(page) for page in stepper.pages]  # noqa: E731
    assert stepper.current == 0 and pages() == [True, False, False, False]
    assert not ui.is_visible(stepper.back) and stepper.next.description == " Next: S1"
    stepper.next.click()  # nothing is done: Next still moves on
    stepper.next.click()
    assert stepper.current == 2 and pages() == [False, False, True, False]
    assert stepper.back.description == " Back: S1"
    stepper.back.click()
    assert stepper.current == 1
    stepper.go(3)
    assert not ui.is_visible(stepper.next) and ui.is_visible(stepper.back)


def test_the_strip_marks_done_current_and_to_do_and_a_chip_opens_its_step():
    stepper = Stepper(_steps(done={0, 2}))
    stepper.chips[2].click()
    assert stepper.current == 2
    marks = [[c for c in chip._dom_classes if c.startswith("mb-chip-")] for chip in stepper.chips]
    assert marks == [["mb-chip-done"], ["mb-chip-todo"], ["mb-chip-current"], ["mb-chip-todo"]]
    assert [chip.description for chip in stepper.chips] == ["✓ 1 S0", "2 S1", "✓ 3 S2", "4 S3"]
    assert stepper.chips[1].tooltip == "line 1"


def test_the_overview_restates_each_step_with_a_change_button():
    stepper = Stepper(_steps(done={1}))
    card = stepper.overview(3)
    rows = card.children[1:]
    assert len(rows) == 3
    assert "S1</b> · line 1" in rows[1].children[0].value and "✓" in rows[1].children[0].value
    rows[2].children[1].click()
    assert stepper.current == 2


# --- the notebook's 13 steps --------------------------------------------------------------------


def test_the_steps_are_in_the_decided_order_with_one_page_on_screen(exp):  # noqa: F811
    nb = stepped(*exp, axes=False)
    assert [step.title for step in nb._stepper.steps] == ORDER
    assert nb.steps() is nb._stepper.widget  # built once
    assert shown(nb) == 0 and nb._stepper.current == 0  # a first check stays on its results
    assert kinds(nb)[:2] == ["current", "todo"]  # the axes are still to confirm
    nb._stepper.chips[1].click()
    assert shown(nb) == 1 and kinds(nb)[0] == "done"
    page = nb._stepper.pages[1]
    assert "Step 2 of 12 · Axes" in page.children[1].children[0].value


def test_the_pages_hold_the_existing_cards_in_the_new_places(exp):  # noqa: F811
    nb = stepped(*exp)
    contains = lambda i, w: _holds(nb._stepper.pages[i], w)  # noqa: E731
    assert contains(0, nb.check_button) and contains(0, nb.masks_card)
    assert contains(1, nb.axis_box) and contains(1, nb.calibration_card)  # one step (owner)
    assert _holds(nb.axis_box, nb.calibration_card) and contains(1, nb.quick_card)
    assert contains(2, nb.condition_rows) and not contains(2, nb.replicate_rows)
    assert contains(3, nb.design_card) and contains(3, nb.replicate_rows)
    assert contains(4, nb.references_card) and contains(5, nb.type_rows.widget)
    assert contains(6, nb.channel_labels.widget) and contains(7, nb.channel_grid.widget)
    assert contains(8, nb.masks_box) and contains(9, nb.check_card)
    assert contains(9, nb.overview_box) and contains(10, nb.run_button)
    assert contains(11, nb.mask_step_card) and not contains(10, nb.mask_step_card)
    # The names rows are split between steps 3 and 4, and still the names card's grid.
    (conditions,) = nb.condition_rows.children
    assert "Condition" in conditions.children[0].children[0].value
    assert list(nb.names_grid.children) == [conditions, *nb.replicate_rows.children]


def _holds(widget, target):
    if widget is target:
        return True
    return any(_holds(child, target) for child in getattr(widget, "children", ()))


def test_the_image_type_has_its_own_step_and_the_grid_no_type_column(exp):  # noqa: F811
    nb = stepped(*exp)
    for row, text in zip(nb.channel_labels.rows, TAGS, strict=True):
        row.id_box.value = text
    nb.channel_labels.confirm.button.click()
    assert len(nb.channel_types.radios) == 4  # one per folder, on the image-type step
    assert not any(_holds(nb.channel_grid.widget, r) for r in nb.channel_types.radios.values())
    assert "Not finished: 4 image type(s)" in nb.type_rows.status.value
    key = next(iter(nb.channel_types.radios))
    nb.channel_types.radios[key].value = "channels"
    assert nb.draft_answers.units[key].image_type == "channels"
    assert "Not finished: 3 image type(s)" in nb.type_rows.status.value
    assert nb.channel_types.radios[key].value == "channels"  # rebuilt from the answer


def test_moving_through_every_page_keeps_every_answer(exp):  # noqa: F811
    nb = stepped(*exp)
    answer_all(nb)
    nb.name_inputs[("conditions", "drug")].value = "Drug A"
    before = snapshot(nb)
    stepper = nb._stepper
    for _ in ORDER:
        stepper.next.click()
    for _ in ORDER:
        stepper.back.click()
    for chip in reversed(stepper.chips):
        chip.click()
    assert snapshot(nb) == before
    assert nb.draft_answers.conditions["drug"].display_name == "Drug A"
    assert all(k in ("done", "noted") for k in kinds(nb)[1:9]) and kinds(nb)[0] == "current"
    assert kinds(nb)[9] == "todo"  # not saved yet
    assert nb.save_experiment()
    assert kinds(nb)[9] == "done"


def test_a_reopened_experiment_opens_on_the_axes_its_only_open_step(exp):  # noqa: F811
    first = stepped(*exp)
    answer_all(first)
    assert first.save_experiment()
    nb = stepped(*exp, axes=False)
    assert nb._stepper.current == 1 and shown(nb) == 1
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    assert all(k in ("done", "noted") for k in kinds(nb)[:10] if k != "current")
    assert "saved " in nb._stepper.chips[9].tooltip
    # The saved reopening folds the cards; a page that shows one unfolds it.
    assert nb._folded and not ui.is_visible(nb.design_card)
    nb._stepper.chips[3].click()
    assert not nb._folded and ui.is_visible(nb.design_card)


def test_a_changed_reopen_turns_the_affected_steps_orange(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1"]):
        add_folder(root, masks, folder, i + 1)
    first = stepped(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    add_folder(root, masks, "drug/rep2", 9)
    nb = stepped(root, masks, out)
    states = nb._stepper.states()
    assert "since saved: 1 new, 0 gone" in states[0].text
    assert states[7].changed and states[9].changed  # the new folder's tags; save again
    assert not states[2].changed and states[2].done  # the conditions did not change
    assert kinds(nb)[7] == "changed" and kinds(nb)[9] == "changed"
    assert nb._stepper.current == 1  # landed on the axes: each session confirms them again


def test_consistency_follows_the_pairing_and_shows_only_when_it_warns(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/day2"]):
        add_folder(root, masks, folder, i + 1)
    nb = stepped(root, masks, out)
    assert _holds(nb._stepper.pages[3], nb.consistency_card)
    assert not _holds(nb._stepper.pages[0], nb.consistency_card)
    assert ui.is_visible(nb.consistency_slot)  # rep2 and day2 are each in one condition only
    assert "on Replicates" in nb._stepper.waits[0].value  # step 1 says where
    (dropdown,) = [
        row.children[2]
        for row in nb.pairing_grid.children[1:]
        if "day2" in row.children[1].children[0].value
    ]
    dropdown.value = "rep2"
    assert not ui.is_visible(nb.consistency_slot)
    assert nb._stepper.waits[0].value == ""
    assert "drug/day2 = rep2" in nb._stepper.states()[3].text


def test_the_axes_page_has_no_stale_image_type_sentence_and_no_broken_picture(exp):  # noqa: F811
    nb = stepped(*exp, axes=False)
    text = "".join(getattr(w, "value", "") for w in nb._stepper.pages[1].children[1].children)
    assert "fluorescence or RGB" not in text
    # never a broken picture: the image is shown only with a plane in it (the one-click card
    # shows the preview of the roles it offers)
    assert ui.is_visible(nb.preview_image) == bool(nb.preview_image.value)
    nb.show_preview()
    assert ui.is_visible(nb.preview_image) and nb.preview_image.value
    described = nb.axis_step()
    assert "fluorescence or RGB" not in described.children[1].children[2].value


# --- PR 17b -------------------------------------------------------------------------------------


def test_pixel_sizes_without_a_physical_size_say_uncalibrated(exp):  # noqa: F811
    nb = stepped(*exp)
    state = nb._stepper.states()[1]  # the pixel size is part of the axes step
    assert state.done and state.note == "uncalibrated"
    assert "confirmed · uncalibrated (pixels)" in state.text
    nb._stepper.go(0)
    assert nb._stepper.chips[1].description == "✓ 2 Axes · uncalibrated"
    assert kinds(nb)[1] == "noted"


def test_pixel_sizes_from_the_headers_are_a_plain_done(tmp_path):
    root, out = tmp_path / "exp", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "drug/rep1"]):
        data = np.stack([blobs(i + 1), blobs(i + 2), blobs(i + 3)])
        path = root / folder / "a.tif"
        path.parent.mkdir(parents=True)
        metadata = {"axes": "CYX", "PhysicalSizeX": 0.65, "PhysicalSizeY": 0.65}
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.steps()
    nb.check_button.click()
    assert not nb._stepper.states()[1].done  # the axes are not confirmed yet
    nb.quick_confirm.click()
    nb._stepper.go(0)
    state = nb._stepper.states()[1]
    assert state.done and not state.note and "0.65" in state.text
    assert kinds(nb)[1] == "done"
