"""Fixes from the owner's test of the v0.5.0 notebook (10 November 2026).

- (g) A list of paths in a status card draws no empty bullet, folded or not.
- (i) Choosing what a condition is compared against keeps the dropdown on the page.
- (j) Every disabled action says, in a visible line beside it, what blocks it.
"""

import re

import numpy as np
import pytest
import tifffile

from test_notebook_check_save import answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_render import blocked_line, issues_html  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.validation import error  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# --- (g) the list of paths -----------------------------------------------------------------------


def _empty_items(html):
    """Return the list items that hold no text of their own before a nested block or the end."""
    return re.findall(r"<li>\s*(?:</li>|<ul|<div|<details)", html)


@pytest.mark.parametrize("count", [1, 5, 20, 21, 90])
def test_a_path_list_in_an_issue_card_has_no_empty_bullet(count):
    paths = [f"ctrl/rep{i}/<a>.tif" for i in range(count)]
    html = issues_html([error("different_channel_counts", "Counts differ.", paths, action="Fix.")])
    assert not _empty_items(html)
    assert "<ul><li><b>" not in html and "<li><ul" not in html  # paths are never list items
    assert html.count("&lt;a&gt;.tif") == count  # every path once, escaped, folded or not
    assert ("more</summary>" in html) == (count > 20)
    if count == 90:
        assert "Show 70 more" in html
    bullets = re.findall(r"<li>(.*?)(?=<li>|</ul>)", html, flags=re.S)
    assert all(_text(item) for item in bullets)  # every bullet says something


def test_a_path_list_alone_keeps_its_fold_and_no_list_markup():
    html = ui.path_list([f"p{i}" for i in range(7)], limit=5)
    assert "<li" not in html and "<ul" not in html
    assert html.count("<div><b>p") == 7 and "Show 2 more" in html
    assert "<details" not in ui.path_list(["a", "b"], limit=5)


def test_a_status_line_that_is_a_path_list_joins_the_line_before_it():
    html = ui.status("warn", "Title", ["Some images:", ui.path_list(["a", "b"]), "Then."], raw=True)
    assert html.count("<li>") == 2 and not _empty_items(html)
    assert ui.status("warn", "T", [ui.path_list(["a"])], raw=True).count("<li>") == 1


# --- (i) the "compared against" dropdown ---------------------------------------------------------


def test_choosing_a_reference_keeps_the_dropdown_and_shows_the_choice_at_once(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    boxes = dict(nb.reference_boxes)
    rows = nb.references_grid.children
    nb.reference_boxes["drug"].value = "ctrl"
    assert (
        nb.reference_boxes["drug"] is boxes["drug"] and nb.reference_boxes["ctrl"] is boxes["ctrl"]
    )
    assert nb.references_grid.children == rows  # the same rows: nothing was rebuilt
    assert boxes["drug"].value == "ctrl"
    assert nb.draft_answers.conditions["drug"].reference == "ctrl"  # saved as before
    assert "the reference of drug" in nb.reference_notes["ctrl"].value
    # "no comparison" is the proposal the Design step shows: saved by its one click, not before.
    assert boxes["ctrl"].value == "" and "ctrl" not in nb.draft_answers.conditions
    nb.design_confirm.click()
    assert nb.reference_boxes["ctrl"] is boxes["ctrl"] and boxes["ctrl"].value == ""
    assert nb.draft_answers.conditions["ctrl"].reference == ""
    assert nb.draft_answers.conditions["drug"].reference == "ctrl"  # the choice is kept


def test_a_refused_reference_is_shown_and_the_dropdown_goes_back_in_place(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    boxes = dict(nb.reference_boxes)
    nb.reference_boxes["drug"].value = "ctrl"
    nb.reference_boxes["ctrl"].value = "drug"  # a loop: refused by the draft's own rule
    assert "This answer was not taken" in nb.references_status.value
    assert nb.reference_boxes["ctrl"] is boxes["ctrl"]
    # Back to the proposal the Design step shows ("no comparison"), which is not saved.
    assert boxes["ctrl"].value == "" and boxes["drug"].value == "ctrl"
    assert nb.draft_answers.conditions.get("ctrl") is None or (
        nb.draft_answers.conditions["ctrl"].reference is None
    )


def test_typing_a_condition_id_relabels_the_references_in_place(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    box = nb.reference_boxes["drug"]
    box.value = "ctrl"
    nb.id_inputs[("conditions", "ctrl")].value = "CONTROL"
    assert nb.reference_boxes["drug"] is box and box.value == "ctrl"
    assert any("CONTROL" in label for label, _ in box.options)


def test_a_new_check_with_other_conditions_builds_new_rows(exp, tmp_path):  # noqa: F811
    nb = stepper(*exp)
    old = dict(nb.reference_boxes)
    other = tmp_path / "other"
    for folder in ("a/rep1", "b/rep1", "c/rep1"):
        (other / folder).mkdir(parents=True)
        data = np.zeros((2, 8, 8), np.uint8)
        tifffile.imwrite(other / folder / "x.tif", data, metadata={"axes": "CYX"})
    nb.masks_folder.value = ""
    nb.folder.value = str(other)
    nb.output.value = str(tmp_path / "out2")
    nb.check_button.click()
    assert sorted(nb.reference_boxes) == ["a", "b", "c"]
    assert not set(map(id, nb.reference_boxes.values())) & set(map(id, old.values()))


# --- (j) what blocks a disabled action -----------------------------------------------------------


def test_blocked_line_is_empty_without_reasons_and_escapes():
    assert blocked_line([]) == ""
    line = blocked_line(["<first>", "second"])
    assert "Not available yet" in line and "&lt;first&gt;; second." in line


def _mixed_channels(tmp_path, images):
    root = tmp_path / "exp"
    for rel, channels in images:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = np.zeros((channels, 16, 16), np.uint16)
        tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "CYX"})
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    return nb


OWNER_DATA = (("ctrl/rep1/a.tif", 3), ("ctrl/rep2/a.tif", 4), ("drug/rep1/a.tif", 3))


def test_the_owner_s_3_and_4_channel_folders_can_be_confirmed(tmp_path):
    # Stage 3 S3.3 (owner decision of 11 November 2026): folders may have different channels.
    nb = _mixed_channels(tmp_path, OWNER_DATA)
    nb.show_preview()
    assert "different channel counts" not in _text(nb.confirm_blocker.value)
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None


def test_confirm_axis_roles_says_that_different_channel_counts_in_one_folder_block_it(tmp_path):
    # Inside one acquisition every image has the same channels (Spec R_UNIT_NOT_HOMOGENEOUS).
    nb = _mixed_channels(tmp_path, (("ctrl/rep1/a.tif", 3), ("ctrl/rep1/b.tif", 4)))
    nb.show_preview()
    assert nb.confirm_button.disabled
    said = _text(nb.confirm_blocker.value)
    assert "Not available yet" in said and "different channel counts" in said
    assert "red message above" in said and "example is not ticked" in said
    nb.preview_checked.button.click()
    assert nb.confirm_button.disabled  # the refusal stays: ticking the preview does not lift it
    said = _text(nb.confirm_blocker.value)
    assert "different channel counts" in said and "example" not in said
    assert nb.confirmed_mapping is None


def test_confirm_axis_roles_names_the_missing_preview_then_the_missing_tick(tmp_path):
    root = tmp_path / "exp"
    for folder in ("ctrl/rep1", "drug/rep1"):
        (root / folder).mkdir(parents=True)
        tifffile.imwrite(
            root / folder / "a.tif", np.zeros((2, 8, 8), np.uint8), metadata={"axes": "TYX"}
        )
    nb = PreprocessingNotebook(str(root))
    nb.folder_step()
    nb.axis_step()
    nb.check_button.click()
    assert nb.confirm_button.disabled and not nb.preview_checked.disabled  # drawn by itself
    assert nb.preview_blocker.value == ""
    assert "click Yes, the example looks right" in _text(nb.confirm_blocker.value)
    nb.role_dropdowns[1].value = "Z"  # Y on no axis: no example can be drawn of these roles
    assert nb.preview_checked.disabled and nb.confirm_button.disabled
    assert "no example of these roles is drawn" in _text(nb.preview_blocker.value)
    nb.role_dropdowns[1].value = "Y"
    assert nb.preview_blocker.value == "" and not nb.preview_checked.disabled
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled and nb.confirm_blocker.value == ""
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None and nb.confirm_blocker.value == ""
    nb.confirm_button.click()  # Change axis roles: the tick is kept, the preview still drawn
    assert nb.confirmed_mapping is None


def test_roles_that_cannot_be_a_mapping_block_the_confirmation_visibly(tmp_path):
    root = tmp_path / "exp"
    (root / "ctrl").mkdir(parents=True)
    tifffile.imwrite(
        root / "ctrl" / "a.tif", np.zeros((2, 8, 8), np.uint8), metadata={"axes": "TYX"}
    )
    nb = PreprocessingNotebook(str(root))
    nb.check_button.click()
    nb.show_preview()
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled
    nb.role_dropdowns[0].value = "X"  # two X: not a mapping
    assert nb.confirm_button.disabled
    assert "these roles have an error" in _text(nb.confirm_blocker.value)


def test_the_run_button_says_what_is_still_to_do(exp):  # noqa: F811
    nb = stepper(*exp)
    assert nb.run_button.disabled
    said = _text(nb.run_blocker.value)
    assert (
        said.startswith("🔒 Not available yet: still to do: ")
        and "more (see Before you run)" in said
    )
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment()
    assert not nb.run_button.disabled and nb.run_blocker.value == ""


def test_the_save_button_says_how_many_answers_are_missing(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    assert nb.save_button.disabled
    assert re.search(r"\d+ answers? still missing", _text(nb.save_blocker.value))
    answer_all(nb)
    assert not nb.save_button.disabled and nb.save_blocker.value == ""
    assert nb.save_experiment()
    assert nb.save_button.disabled and nb.save_blocker.value == ""  # saved: nothing is blocked


def test_the_stains_confirmation_says_when_no_label_is_typed(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    labels = nb.channel_labels
    for row in labels.rows:
        row.id_box.value = ""
    assert labels.confirm.disabled and "no label is typed" in _text(labels.problem.value)
    labels.rows[0].id_box.value = "DNA"
    assert not labels.confirm.disabled and "no label" not in labels.problem.value


def test_the_named_axis_copies_button_says_what_it_waits_for(tmp_path):
    root = tmp_path / "exp"
    (root / "ctrl").mkdir(parents=True)
    data = np.zeros((3, 16, 16), np.uint16)
    tifffile.imwrite(root / "ctrl" / "a.tif", data, photometric="minisblack", metadata=None)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.check_button.click()
    nb.role_dropdowns[0].value = "C"
    assert ui.is_visible(nb.convert_card) and nb.convert_button.disabled
    assert "roles of the copies are not confirmed" in _text(nb.convert_blocker.value)
    assert "example is not ticked" in _text(nb.convert_tick_blocker.value)
    nb.show_preview()
    nb.preview_checked.button.click()
    assert nb.convert_tick_blocker.value == ""
    nb.convert_roles_checked.button.click()
    assert "no folder for the copies is given" in _text(nb.convert_blocker.value)
    nb.convert_folder.value = str(tmp_path / "copies")
    assert not nb.convert_button.disabled and nb.convert_blocker.value == ""
