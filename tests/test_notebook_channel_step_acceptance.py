"""Acceptance: the guided channel step of the shipped notebook, button by button (25 Nov 2026).

The owner's design, driven headlessly the way a user clicks through the stepper
(``PreprocessingNotebook().steps()``, the shipped notebooks' layout): Images (check), Axes
(confirm), Channels (image type, labels, then the guided step), the rest of the answers, Check
and save. Each scenario checks what the user SEES (the questions asked, the number of rows of
dropdowns, the choices kept, nothing saved before Submit) and what is PUBLISHED (the
description passes the Spec's reference validator and records exactly the intended channels of
every imaging unit, in fast mode only when the order is the same for the whole dataset with
one channel count).

The same scenarios, as clicks for a person, are in ``docs/colab-release-check.md``.
"""

from __future__ import annotations

import tomllib

import pytest

from channel_images import write_channels, write_masks, write_rgb
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from spec_oracle import refval

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_channels_guide import (  # noqa: E402
    NARROW_QUESTION,
    SAME_QUESTION,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

TAGS3 = ["DNA", "ACTIN", "PHASE"]
LABELS4 = [*TAGS3, "EXTRA"]
KINDS = ["blobs", "filaments", "cells", "blobs"]
UNITS = [(c, b, "single") for c in ("ctrl", "drug") for b in ("rep1", "rep2", "rep3")]


def folder(key):
    return "/".join(part for part in key if part != "single")


def experiment(tmp_path, *, count=lambda key: 3, names=lambda key, n: None, rgb=lambda key: False):
    """Write one image per unit; ``count``, ``names`` and ``rgb`` say what each unit holds."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, key in enumerate(UNITS):
        path = root / folder(key) / "a.tif"
        if rgb(key):
            write_rgb(path, seed=i + 1)
        else:
            n = count(key)
            write_channels(path, names(key, n), KINDS[:n], seed=i + 1)
        write_masks(masks, {f"{folder(key)}/a.tif": i + 1}, {"Nuclei": "labels"})
    return root, masks, tmp_path / "out"


def opened(root, masks, out, labels, *, typed=True):
    """Click through Images and Axes, confirm the image type and the labels (Channels)."""
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.welcome()
    nb.steps()
    nb.results_step()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    nb._stepper.go(2)  # Channels
    if typed:
        nb.type_line.apply.click()  # "Use for all folders": the image type first
    card = nb.channel_labels
    if labels:
        while len(card.rows) < len(labels):
            card.add_button.click()
        for row, text in zip(card.rows, labels, strict=False):
            row.id_box.value = text
        card.confirm.button.click()
    return nb


def draft(out):
    """Return the draft file's bytes (None before the first save)."""
    path = out / DRAFT_FILENAME
    return path.read_bytes() if path.exists() else None


def choose(nb, row, tags):
    """Pick ``tags`` in the dropdowns of one row of the guided step."""
    for drop, tag in zip(nb.channel_grid.guide.drops[row], tags, strict=True):
        drop.value = tag


def shown(nb):
    """Return what every row of the form shows now."""
    return nb.channel_grid.guide.values()


def finish(nb):
    """Answer the rest (mask type, comparisons, design, same way) as a user does."""
    question = nb.mask_card.questions.get("Nuclei")
    if question is not None and question.choice.value is None:
        question.choice.value = "instances"
    if nb._chosen_reference("drug") is None:
        nb.reference_boxes["drug"].value = "ctrl"
    if nb._chosen_reference("ctrl") is None and nb.reference_boxes["ctrl"].value is None:
        nb.reference_boxes["ctrl"].value = ""
    if not nb._design_confirmed():
        nb.design_confirm.click()
    for toggle in list(nb.same_way.values()):
        if not toggle.value:
            toggle.button.click()


def published(out):
    """Return the published description, after the Spec's reference validator passed it."""
    data = (out / DESCRIPTION_FILENAME).read_bytes()
    report, _ = refval().validate(data)
    assert report.errors == []
    return tomllib.loads(data.decode("utf-8"))


def unit_channels(doc):
    """Return each published unit's channels and marker, by its folder (condition/repeat)."""
    found = {}
    for repeat in doc["repeats"].values():
        for condition, member in repeat["members"].items():
            for unit in member["technical_repeats"].values():
                key = (condition, member["source_repeat"], unit["source_folder"])
                found[folder(key)] = (unit["channels"], unit.get("channels_from"))
    return found


def visible(widget):
    return ui.is_visible(widget)


# --- 1. the owner's layout: 3 channels everywhere, a 4th in some repeats ------------------------


def test_1_owner_layout_yes_opens_one_row_per_channel_count_and_publishes(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, LABELS4)
    guide = nb.channel_grid.guide
    assert SAME_QUESTION in guide.same_text.value and "3 and 4 channels" in guide.same_text.value
    assert not guide.rows and not visible(guide.submit_button)  # nothing before the answer
    before = draft(exp[2])
    guide.same.value = "yes"
    assert [r.key for r in guide.rows] == [("whole", 3), ("whole", 4)]  # one row per count
    assert [len(guide.drops[r.key]) for r in guide.rows] == [3, 4]
    assert not visible(guide.narrow_box)
    choose(nb, ("whole", 3), TAGS3)
    choose(nb, ("whole", 4), LABELS4)
    assert draft(exp[2]) == before  # nothing saved before Submit
    assert guide.submit()
    assert "Saved" in guide.message.value
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc  # several counts: a strict per-unit file
    expected = {folder(k): (LABELS4 if k[1] == "rep2" else TAGS3, None) for k in UNITS}
    assert unit_channels(doc) == expected


# --- 2. one channel count, the same order everywhere ---------------------------------------------


def test_2_same_count_yes_is_one_row_and_publishes_fast_mode(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    assert guide.same_text.value == f"<b>{SAME_QUESTION}</b>"  # no count sentence
    guide.same.value = "yes"
    assert [r.key for r in guide.rows] == [("whole", 3)]
    choose(nb, ("whole", 3), TAGS3)
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert doc["channel_layout"]["channels"] == TAGS3  # ADR 0006: the user's statement
    assert unit_channels(doc) == {folder(k): (TAGS3, "experiment") for k in UNITS}


# --- 3. one count, the order is per biological repeat ---------------------------------------------


def test_3_same_count_no_per_repeat_one_row_per_repeat_with_different_orders(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    assert visible(guide.narrow_box) and NARROW_QUESTION in repr(guide.narrow_box.children[0])
    assert not guide.rows  # the second question first
    guide.narrow.value = "repeat"
    assert [r.key for r in guide.rows] == [("repeat", f"rep{i}", 3) for i in (1, 2, 3)]
    orders = {
        "rep1": TAGS3,
        "rep2": ["ACTIN", "DNA", "PHASE"],
        "rep3": ["PHASE", "ACTIN", "DNA"],
    }
    for rep, tags in orders.items():
        choose(nb, ("repeat", rep, 3), tags)
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (orders[k[1]], None) for k in UNITS}


# --- 4. one count, every imaging unit -------------------------------------------------------------


def test_4_same_count_no_per_unit_one_row_per_unit(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "unit"
    assert len(guide.rows) == len(UNITS)
    orders = {k: TAGS3[i % 3 :] + TAGS3[: i % 3] for i, k in enumerate(sorted(UNITS))}
    for key, tags in orders.items():
        choose(nb, ("unit", key), tags)
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (orders[k], None) for k in UNITS}


# --- 5. mixed counts, per biological repeat -------------------------------------------------------


def test_5_mixed_counts_no_per_repeat(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep3" else 3)
    nb = opened(*exp, LABELS4)
    guide = nb.channel_grid.guide
    assert "3 and 4 channels" in guide.same_text.value
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    assert [r.key for r in guide.rows] == [
        ("repeat", "rep1", 3),
        ("repeat", "rep2", 3),
        ("repeat", "rep3", 4),
    ]
    orders = {"rep1": TAGS3, "rep2": ["ACTIN", "DNA", "PHASE"], "rep3": ["EXTRA", *TAGS3]}
    for rep, tags in orders.items():
        choose(nb, ("repeat", rep, len(tags)), tags)
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (orders[k[1]], None) for k in UNITS}


# --- 6. a header contradicts the submitted tags --------------------------------------------------

SWAPPED = ("drug", "rep2", "single")


def _named(key, n):
    return ["ACTIN", "DNA", "PHASE"] if key == SWAPPED else list(TAGS3)


def test_6_a_contradicting_unit_is_refused_on_submit_and_released_by_its_confirm(tmp_path):
    exp = experiment(tmp_path, names=_named)
    nb = opened(*exp, TAGS3)
    guide, rows = nb.channel_grid.guide, nb.channel_grid.by_repeat
    guide.same.value = "yes"
    assert shown(nb) == {("whole", 3): [None, None, None]}  # the headers differ: no proposal
    choose(nb, ("whole", 3), TAGS3)
    assert guide.submit()
    assert "1 imaging unit(s) not tagged" in guide.message.value
    assert set(rows.listed()) == {SWAPPED}
    assert "C0: header ‘ACTIN’ is ACTIN, the row says DNA" in rows.listed()[SWAPPED]
    units = nb.draft_answers.units
    assert SWAPPED not in units or not any(units[SWAPPED].channels)  # never reordered
    review = nb.draft_answers.tag_review
    assert set(review.refused) == {SWAPPED}  # the refusal is in the same write as the tags
    finish(nb)
    assert not nb.save_experiment()  # a listed unit is never published
    rows.override.buttons[SWAPPED].click()  # the existing refusal flow
    for index, tag in enumerate(["ACTIN", "DNA", "PHASE"]):
        rows.override.cells[(SWAPPED, index)].value = tag
    assert rows.override.confirm()
    assert rows.listed() == {}
    finish(nb)
    assert nb.save_experiment()
    found = unit_channels(published(exp[2]))
    assert found[folder(SWAPPED)] == (["ACTIN", "DNA", "PHASE"], "unit")  # its own list
    assert all(v == (TAGS3, "experiment") for k, v in found.items() if k != folder(SWAPPED))


# --- 7. colour (RGB) ------------------------------------------------------------------------------


def test_7_every_folder_colour_asks_nothing_and_publishes_r_g_b(tmp_path):
    exp = experiment(tmp_path, rgb=lambda key: True)
    nb = opened(*exp, [])
    guide = nb.channel_grid.guide
    assert nb.channel_labels.not_applicable  # no stains to name
    assert not visible(guide.questions) and not guide.rows
    assert "Every folder is colour (RGB)" in guide.rows_box.children[0].value
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    # "Use for all folders: colour" states it for the whole dataset (ADR 0006), as before
    assert doc["channel_layout"]["channels"] == ["R", "G", "B"]
    found = unit_channels(doc)
    assert all(value == (["R", "G", "B"], "experiment") for value in found.values())
    assert len(found) == len(UNITS)


def test_7_the_image_type_is_asked_before_any_row(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3, typed=False)
    guide = nb.channel_grid.guide
    assert "Waiting for the image type" in guide.rows_box.children[0].value
    assert not visible(guide.questions)


def test_7_colour_mixed_with_separate_channels_is_refused_as_today(tmp_path):
    exp = experiment(tmp_path, rgb=lambda key: key[1] == "rep3")
    nb = opened(*exp, [], typed=False)
    # colour (YXS) and separate channels (CYX) have no one set of axis roles: refused at Axes,
    # so Channels never opens and nothing can be tagged or published
    assert nb.confirmed_mapping is None
    assert "Waiting for the axis roles" in nb.channels_waiting.value
    assert not visible(nb.channel_grid.widget) and not nb.channel_grid.guide.rows
    finish(nb)
    assert not nb.save_experiment()
    assert not (exp[2] / DESCRIPTION_FILENAME).exists()


# --- 8. changing one's mind before Submit ---------------------------------------------------------


def test_8_choices_are_kept_across_changes_and_branch_switches_with_no_draft_write(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, LABELS4)
    guide = nb.channel_grid.guide
    before = draft(exp[2])
    guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    drop = guide.drops[("whole", 3)][1]
    drop.value = "PHASE"  # change your mind
    drop.value = "ACTIN"  # and back
    assert shown(nb)[("whole", 3)] == TAGS3
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    rows = shown(nb)
    assert set(rows) == {("repeat", "rep1", 3), ("repeat", "rep2", 4), ("repeat", "rep3", 3)}
    assert rows[("repeat", "rep1", 3)] == TAGS3  # starts from the whole dataset's choice
    assert rows[("repeat", "rep2", 4)] == [None] * 4  # no choice for 4 channels yet
    choose(nb, ("repeat", "rep3", 3), ["ACTIN", "DNA", "PHASE"])
    guide.narrow.value = "unit"
    assert len(shown(nb)) == len(UNITS)
    assert shown(nb)[("unit", ("ctrl", "rep3", "single"))] == ["ACTIN", "DNA", "PHASE"]
    guide.same.value = "yes"
    assert shown(nb) == {("whole", 3): TAGS3, ("whole", 4): [None] * 4}  # no stale rows
    guide.same.value = "no"
    assert shown(nb)[("unit", ("ctrl", "rep3", "single"))] == ["ACTIN", "DNA", "PHASE"]
    assert draft(exp[2]) == before  # not one draft write before Submit
    assert nb.draft_answers.units == {} or all(
        not any(u.channels) for u in nb.draft_answers.units.values()
    )


# --- 9. one image-type answer for all folders (owner, 25 November 2026) ---------------------


def _type_for_all(nb, value):
    """Choose the image type once and click "Use for all folders"."""
    nb.type_line.choice.value = value
    nb.type_line.apply.click()


def _types(nb):
    return {k: u.image_type for k, u in nb.draft_answers.units.items()}


def test_9a_mixed_channel_counts_one_separate_channels_answer_goes_straight_to_tagging(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, LABELS4, typed=False)
    assert visible(nb.type_line.apply)  # offered whatever the channel counts
    _type_for_all(nb, "channels")
    assert _types(nb) == {k: "channels" for k in UNITS}  # one click, every folder
    assert nb.type_line.followups.value == ""
    guide = nb.channel_grid.guide
    assert visible(guide.questions) and "3 and 4 channels" in guide.same_text.value


def test_9b_different_image_counts_per_folder_one_answer_for_all(tmp_path):
    exp = experiment(tmp_path)
    root, masks, _ = exp
    for extra, key in enumerate(UNITS[:2]):  # two folders hold three images, the others one
        for name in ("b.tif", "c.tif"):
            write_channels(root / folder(key) / name, None, KINDS[:3], seed=40 + extra)
            write_masks(masks, {f"{folder(key)}/{name}": 40 + extra}, {"Nuclei": "labels"})
    nb = opened(*exp, TAGS3, typed=False)
    _type_for_all(nb, "channels")
    assert _types(nb) == {k: "channels" for k in UNITS}
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    assert [r.key for r in guide.rows] == [("whole", 3)]
    choose(nb, ("whole", 3), TAGS3)
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    assert unit_channels(published(exp[2])) == {folder(k): (TAGS3, "experiment") for k in UNITS}


HEADER_RGB = ("drug", "rep3", "single")


def scanner_experiment(tmp_path):
    """Colour images, three interleaved samples each: one folder's header says colour (RGB),
    the others' do not (a scanner's MINISBLACK header, Spec RH1)."""
    import numpy as np
    import tifffile

    from channel_images import blobs

    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, key in enumerate(UNITS):
        path = root / folder(key) / "a.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = np.stack([blobs(i + s) // 256 for s in (1, 2, 3)], axis=-1).astype(np.uint8)
        photometric = "rgb" if key == HEADER_RGB else "minisblack"
        tifffile.imwrite(path, data, photometric=photometric, planarconfig="contig")
        write_masks(masks, {f"{folder(key)}/a.tif": i + 1}, {"Nuclei": "labels"})
    return root, masks, tmp_path / "out"


def test_9c_one_header_says_colour_answered_separate_for_all_lists_only_that_folder(tmp_path):
    exp = scanner_experiment(tmp_path)
    nb = opened(*exp, [], typed=False)
    line = nb.type_line
    assert line.suggestion() is None and visible(line.apply)  # headers differ: still one answer
    assert "choose one type for all folders in the box" in line.line.value
    assert line.per_folder.selected_index is None
    _type_for_all(nb, "channels")
    assert _types(nb) == {k: "channels" for k in UNITS}  # recorded for every folder
    later = line.followups.value
    assert "Another step for 1 folder:" in later
    assert folder(HEADER_RGB).replace("/", " / ") in later
    assert "R_IMAGE_TYPE_CONTRADICTS_HEADER" in later
    # the others go on: the channel step opens with its question
    card = nb.channel_labels
    for row, text in zip(card.rows, TAGS3, strict=False):
        row.id_box.value = text
    card.confirm.button.click()
    assert visible(nb.channel_grid.guide.questions)


def test_9c_colour_for_all_offers_the_rgb_header_override_to_the_other_folders(tmp_path):
    exp = scanner_experiment(tmp_path)
    nb = opened(*exp, [], typed=False)
    _type_for_all(nb, "rgb")
    assert _types(nb) == {k: "rgb" for k in UNITS}
    later = nb.type_line.followups.value
    assert "Another step for 5 folders:" in later and "RGB header override" in later
    assert folder(HEADER_RGB).replace("/", " / ") not in later  # its header says colour
    before = draft(exp[2])
    nb.type_line.override.box.value = True  # the existing RH1 tick, for those folders
    assert draft(exp[2]) == before  # a form choice: nothing saved before Validate
    nb.type_line.apply.click()  # Validate
    assert nb.type_line.followups.value == ""
    assert all(u.rgb_header_override for k, u in nb.draft_answers.units.items() if k != HEADER_RGB)
    finish(nb)
    assert nb.save_experiment()
    found = unit_channels(published(exp[2]))
    assert all(value[0] == ["R", "G", "B"] for value in found.values())


def test_9_colour_for_all_is_refused_naming_every_folder_without_three_channels(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, [], typed=False)
    _type_for_all(nb, "rgb")
    assert _types(nb) == {}  # all or none
    said = nb.type_line.message.value
    assert "a colour image has 3 channels" in said
    assert said.count("(4 channels)") == 2


# --- 10. the image type is a form too: Validate is its one save (owner, 25 November 2026) ----


def test_10_image_type_choices_write_nothing_and_redraw_nothing_until_validate(
    tmp_path, monkeypatch
):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, [], typed=False)
    line = nb.type_line
    assert line.apply.description.strip() == "Validate"
    line.per_folder.selected_index = 0  # Set per folder… (its rows are built once opened)
    radios = nb.channel_types.radios
    assert len(radios) == len(UNITS)
    before = draft(exp[2])
    calls = {"rows": 0, "line": 0, "saves": 0}
    rows_refresh, line_refresh, save = nb.type_rows.refresh, line.refresh, nb._save_answers

    def count(name, function):
        def counted(*args, **kwargs):
            calls[name] += 1
            return function(*args, **kwargs)

        return counted

    monkeypatch.setattr(nb.type_rows, "refresh", count("rows", rows_refresh))
    monkeypatch.setattr(line, "refresh", count("line", line_refresh))
    monkeypatch.setattr(nb, "_save_answers", count("saves", save))
    widgets_before = dict(radios)
    line.choice.value = "channels"  # the box: every folder without its own choice follows
    assert all(r.value == "channels" for r in radios.values())
    one = ("drug", "rep1", "single")
    radios[one].value = "rgb"  # one folder on its own, then back: the values stay as chosen
    radios[one].value = "channels"
    radios[one].value = "rgb"
    assert radios[one].value == "rgb" and "Not validated yet" in nb.channel_types.notes[one].value
    assert calls == {"rows": 0, "line": 0, "saves": 0}  # no save, no redraw
    assert dict(nb.channel_types.radios) == widgets_before  # the very same widgets
    assert draft(exp[2]) == before
    radios[one].value = "channels"
    line.apply.click()  # Validate: one draft write for every folder's type
    assert calls["saves"] == 1
    assert _types(nb) == {k: "channels" for k in UNITS}
    card = nb.channel_labels  # the channel step goes on: the labels, then the question
    while len(card.rows) < len(LABELS4):
        card.add_button.click()
    for row, text in zip(card.rows, LABELS4, strict=True):
        row.id_box.value = text
    card.confirm.button.click()
    assert visible(nb.channel_grid.guide.questions)


def test_10_validate_refuses_a_form_with_a_folder_left_unanswered(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, [], typed=False)
    line = nb.type_line
    line.per_folder.selected_index = 0
    line.choice.value = None  # the box's proposal from the headers taken away
    one = ("ctrl", "rep1", "single")
    nb.channel_types.radios[one].value = "channels"  # one folder only, no answer for all
    assert line.apply.disabled and "5 left" in line.blocker.value
    assert not line._validate()
    assert "choose the type of every folder" in line.message.value
    assert _types(nb) == {}
