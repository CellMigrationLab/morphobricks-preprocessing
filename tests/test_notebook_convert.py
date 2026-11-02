"""PP-11b: the notebook's **Make named-axis copies** card (contract §17, plan §10).

When step 2's axis check reports images whose header names their axes wrongly or not at
all, the card offers what the command line's ``convert`` does: a required folder for the
copies (the CLI's folder rule), the roles the user confirmed (nothing taken from the
headers; disabled until confirmed), what will be converted and copied byte for byte (the
CLI's planning), the space, then the copies under the copy folder's lock, with refusals and
the stale-lock button on the existing cards. After the copies, the record and the next
step: the copies checked in step 1 with the same output folder, then the run.
"""

import importlib
import json
import os
import re
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    CONVERSION_RECORD_FILENAME,
    AxisMapping,
    convert_experiment,
    read_conversion_record,
)
from morphobricks_preprocessing.conversion import plan_conversion
from morphobricks_preprocessing.run.lock import LOCK_FILENAME, output_lock
from test_convert_run import plain_experiment  # plain stacks: axes_not_named

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
ui = importlib.import_module("morphobricks_preprocessing.notebook_style")
PreprocessingNotebook = importlib.import_module(
    "morphobricks_preprocessing.notebook_ui"
).PreprocessingNotebook

NAMES = ("ctrl/r1/a.tif", "ctrl/r1/b.tif", "drug/r1/a.tif")
SWAPPED = AxisMapping("ZYX", ("Z", "X", "Y"))


def swapped_experiment(root, names=NAMES, sizes=(0.5, 0.7)):
    """OME images whose header names Y and X the other way round from the truth."""
    rng = np.random.default_rng(1)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = rng.integers(0, 255, (2, 6, 9), dtype=np.uint8)
        metadata = {"axes": "ZYX", "PhysicalSizeX": sizes[0], "PhysicalSizeY": sizes[1]}
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
    return root


def text(widget):
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", widget.value))


def output_of(root):
    return root.parent / f"{root.name}_MorphoBricks_Preprocessing"


def checked(root, *, show_output=True, exclude=""):
    nb = PreprocessingNotebook(str(root))
    nb.welcome()
    nb.folder_step(show_output=show_output)
    page = nb.axis_step()
    nb.run_step()
    nb.exclude.value = exclude
    nb.check_button.click()
    return nb, page


def swap_roles(nb):
    """Choose the true roles: the header's Y is X and its X is Y."""
    nb.role_dropdowns[1].value = "X"
    nb.role_dropdowns[2].value = "Y"


def confirm_copy_roles(nb):
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.convert_roles_checked.button.click()


def ready_to_convert(tmp_path, copies=None, **kwargs):
    root = swapped_experiment(tmp_path / "exp")
    nb, page = checked(root, **kwargs)
    swap_roles(nb)
    confirm_copy_roles(nb)
    nb.convert_folder.value = str(copies or tmp_path / "copies")
    return nb, root


# Offered only for the axis check's images -----------------------------------------------


def test_the_card_is_offered_under_the_axis_check_for_swapped_axes(tmp_path):
    root = swapped_experiment(tmp_path / "exp")
    nb, page = checked(root)
    assert nb.convert_card in nb.axis_box.children
    assert not ui.is_visible(nb.convert_card)  # the header's roles agree: nothing to convert
    swap_roles(nb)
    assert "swap the Y and X" in text(nb.axis_status)
    assert ui.is_visible(nb.convert_card)
    children = list(nb.axis_box.children)
    assert children.index(nb.convert_card) == children.index(nb.axis_status) + 1
    nb.role_dropdowns[1].value, nb.role_dropdowns[2].value = "Y", "X"
    assert not ui.is_visible(nb.convert_card)


def test_the_card_is_offered_for_plain_stacks_and_uses_the_confirmed_roles(tmp_path):
    root = plain_experiment(tmp_path / "exp")
    nb, _ = checked(root)
    nb.role_dropdowns[0].value = "Z"
    assert ui.is_visible(nb.convert_card)  # axes_not_named: a warning, the roles can be confirmed
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None
    # Roles confirmed in step 2 need no second answer on the card.
    assert not ui.is_visible(nb.convert_roles_checked.widget)
    assert "confirmed in step 2" in text(nb.convert_roles)
    nb.convert_folder.value = str(tmp_path / "copies")
    assert not nb.convert_button.disabled
    nb.convert_button.click()
    record, _ = read_conversion_record(tmp_path / "copies")
    assert {e.reason for e in record.files} == {"axes_not_named"}
    assert record.mapping.roles == ("Z", "Y", "X")


def test_an_embedding_brick_that_hides_the_output_folder_does_not_get_the_card(tmp_path):
    root = swapped_experiment(tmp_path / "exp")
    nb, _ = checked(root, show_output=False)
    swap_roles(nb)
    assert "swap the Y and X" in text(nb.axis_status)
    assert not ui.is_visible(nb.convert_card)


# Nothing guessed: the roles must be confirmed ---------------------------------------------


def test_the_button_waits_for_the_roles_and_the_folder(tmp_path):
    root = swapped_experiment(tmp_path / "exp")
    nb, _ = checked(root)
    swap_roles(nb)
    nb.convert_folder.value = str(tmp_path / "copies")
    assert nb.convert_button.disabled
    assert nb.convert_roles_checked.disabled  # the preview comes first
    assert "not confirmed yet" in text(nb.convert_roles)
    assert "Waiting for the roles" in text(nb.convert_plan)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert not nb.convert_roles_checked.disabled and nb.convert_button.disabled
    nb.convert_roles_checked.button.click()
    assert not nb.convert_button.disabled
    shown = text(nb.convert_roles)
    assert "Y (height) in the file → X" in shown and "X (width) in the file → Y" in shown
    # Changing a role takes the answer back: the copies are never written with other roles.
    nb.role_dropdowns[0].value = "T"
    assert not nb.convert_roles_checked.value and nb.convert_button.disabled
    assert not (tmp_path / "copies").exists()


def test_the_folder_is_required_and_checked_with_the_commands_rule(tmp_path):
    nb, root = ready_to_convert(tmp_path, copies=tmp_path / "exp" / "copies")
    assert "copy folder overlaps experiment" in text(nb.convert_plan).lower()
    assert nb.convert_button.disabled
    nb.convert_folder.value = str(tmp_path)  # holds the experiment
    assert "copy folder overlaps experiment" in text(nb.convert_plan).lower()
    (tmp_path / "a_file").write_text("x")
    nb.convert_folder.value = str(tmp_path / "a_file")
    assert "copy folder not a folder" in text(nb.convert_plan).lower()
    nb.convert_folder.value = ""
    assert "Waiting for the roles and the folder" in text(nb.convert_plan)
    assert nb.convert_button.disabled
    nb.convert_folder.value = str(tmp_path / "copies")
    assert not nb.convert_button.disabled


def test_browse_without_a_dialog_names_colabs_copy_path(tmp_path, monkeypatch):
    nb, _ = ready_to_convert(tmp_path)
    monkeypatch.setattr(ui, "ask_directory", lambda initial="": None)
    nb.convert_browse.click()
    assert "Copy path" in nb.convert_status.value
    monkeypatch.setattr(ui, "ask_directory", lambda initial="": str(tmp_path / "chosen"))
    nb.convert_browse.click()
    assert nb.convert_folder.value == str(tmp_path / "chosen")
    assert str(tmp_path / "chosen") in text(nb.convert_plan)


# What will happen, from the command's planning --------------------------------------------


def test_the_plan_shows_what_is_converted_and_copied_and_the_space(tmp_path):
    root = swapped_experiment(tmp_path / "exp")
    nb, _ = checked(root, exclude="drug/r1/a.tif")
    swap_roles(nb)
    confirm_copy_roles(nb)
    nb.convert_folder.value = str(tmp_path / "copies")
    plan = text(nb.convert_plan)
    assert "Written as named-axis OME-TIFF with your roles: 2 images" in plan
    assert "ctrl/r1/a.tif (axes_yx_swapped)" in plan
    assert "Copied byte for byte" in plan and "drug/r1/a.tif (excluded)" in plan
    assert "Space needed" in plan and "Free on that drive" in plan
    assert not (tmp_path / "copies").exists()  # planning writes nothing
    from morphobricks_preprocessing import preview_conversion  # new in PP-11b

    expected = preview_conversion(root, SWAPPED, tmp_path / "copies", exclude=["drug/r1/a.tif"])
    # The same plan as the command's; the free space may change between the two calls.
    shown = nb._convert_preview
    assert replace(shown, free_bytes=None) == replace(expected, free_bytes=None)


def test_not_enough_free_space_disables_the_button(tmp_path, monkeypatch):
    plan_module = importlib.import_module("morphobricks_preprocessing.conversion_plan")
    monkeypatch.setattr(plan_module, "free_space", lambda path: 10)
    nb, _ = ready_to_convert(tmp_path)
    assert "does not have enough free space" in text(nb.convert_plan)
    assert nb.convert_button.disabled


def test_preview_conversion_is_the_commands_plan_and_writes_nothing(tmp_path):
    from morphobricks_preprocessing import preview_conversion  # new in PP-11b

    root = swapped_experiment(tmp_path / "exp")
    dest = tmp_path / "copies"
    preview = preview_conversion(root, SWAPPED, dest)
    planned, issues = plan_conversion(root.resolve(), SWAPPED, dest.resolve())
    assert preview.ok and preview.fits and not preview.earlier_conversion
    assert preview.converted == tuple((p.relative_path, "axes_yx_swapped") for p in planned)
    assert preview.copied == ()
    assert preview.needed_bytes == sum(p.nbytes for p in planned) == 3 * 2 * 6 * 9
    assert preview.free_bytes is not None and not dest.exists()
    assert [i.code for i in preview_conversion(root, SWAPPED, root / "x").issues] == [
        "copy_folder_overlaps_experiment"
    ]
    convert_experiment(root, SWAPPED, dest)
    assert preview_conversion(root, SWAPPED, dest).earlier_conversion


# The copies, refusals and the lock ----------------------------------------------------------


def test_the_copies_are_made_with_a_progress_bar_and_the_record_is_shown(tmp_path):
    nb, root = ready_to_convert(tmp_path)
    nb.convert_button.click()
    copies = tmp_path / "copies"
    record, sha256 = read_conversion_record(copies)
    assert [e.relative_path for e in record.files] == sorted(NAMES)
    assert record.mapping == SWAPPED and record.source_root == root.as_posix()
    assert nb.convert_progress.bar_style == "success" and ui.is_visible(nb.convert_progress)
    assert nb.convert_progress.value == nb.convert_progress.max == 3
    done = text(nb.convert_status)
    assert "The named-axis copies are made" in done
    assert str(copies / CONVERSION_RECORD_FILENAME) in done
    assert "Written as OME-TIFF with your roles: 3 images" in done
    assert ui.is_visible(nb.convert_recheck_button)
    assert nb.convert_button.description.strip() == "Make named-axis copies"
    # Repeating it reuses the copies and keeps the record.
    nb.convert_button.click()
    assert "Already there and unchanged: 3 images" in text(nb.convert_status)
    assert read_conversion_record(copies)[1] == sha256


def test_pixel_sizes_of_a_relabelled_y_and_x_are_stated_as_unknown(tmp_path):
    nb, _ = ready_to_convert(tmp_path)
    nb.convert_button.click()
    after = text(nb.convert_result)
    assert "Pixel sizes: 3 copies" in after and "ctrl/r1/a.tif: X, Y" in after
    assert "under Pixel sizes in step 1" in after


def test_equal_x_and_y_sizes_are_carried_so_nothing_is_said_about_them(tmp_path):
    root = swapped_experiment(tmp_path / "exp", sizes=(0.5, 0.5))
    nb, _ = checked(root)
    swap_roles(nb)
    confirm_copy_roles(nb)
    nb.convert_folder.value = str(tmp_path / "copies")
    nb.convert_button.click()
    assert "The named-axis copies are made" in text(nb.convert_status)
    assert "Pixel sizes:" not in text(nb.convert_result)


def test_a_folder_that_is_not_empty_is_refused_on_the_card(tmp_path):
    copies = tmp_path / "copies"
    copies.mkdir()
    (copies / "notes.txt").write_text("mine")
    nb, _ = ready_to_convert(tmp_path)
    nb.convert_button.click()
    assert "No copies were made." in text(nb.convert_status)
    assert "copy folder not empty" in text(nb.convert_result).lower()
    assert nb.convert_progress.bar_style == "danger"
    assert sorted(p.name for p in copies.iterdir()) == ["notes.txt"]
    assert not ui.is_visible(nb.convert_recheck_button)


def test_a_conversion_going_on_shows_the_lock_card_and_writes_nothing(tmp_path):
    copies = tmp_path / "copies"
    copies.mkdir()
    nb, _ = ready_to_convert(tmp_path)
    with output_lock(copies):
        nb.convert_button.click()
        assert "the folder for the copies is locked" in text(nb.convert_status)
        assert ui.is_visible(nb.convert_lock.card)
        assert "Another conversion holds the lock" in text(nb.convert_lock.holder)
        assert "The folder for the copies is locked" in nb.convert_lock.card.children[0].value
    assert sorted(p.name for p in copies.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="only POSIX can tell a process runs")
def test_the_lock_of_a_conversion_running_here_is_kept(tmp_path):
    copies = tmp_path / "copies"
    copies.mkdir()
    nb, _ = ready_to_convert(tmp_path)
    with output_lock(copies):
        nb.convert_button.click()
        nb.convert_lock.button.click()  # a process running here: its lock is kept
        assert "The lock was kept" in nb.convert_lock.result.value
        assert (copies / LOCK_FILENAME).exists()


def test_the_stale_lock_of_a_stopped_conversion_is_removed_only_on_the_button(tmp_path):
    copies = tmp_path / "copies"
    copies.mkdir()
    holder = {"host": "another-pc", "pid": 4242, "started_at": "2026-11-02T10:00:00+00:00"}
    (copies / LOCK_FILENAME).write_text(json.dumps(holder), encoding="utf-8")
    nb, _ = ready_to_convert(tmp_path)
    nb.convert_button.click()
    assert ui.is_visible(nb.convert_lock.card)
    assert "another-pc" in nb.convert_lock.holder.value
    assert (copies / LOCK_FILENAME).exists()
    nb.convert_lock.button.click()
    assert not (copies / LOCK_FILENAME).exists()
    assert "Then click Make named-axis copies again." in nb.convert_lock.result.value
    nb.convert_button.click()
    assert "The named-axis copies are made" in text(nb.convert_status)
    assert not ui.is_visible(nb.convert_lock.card)


def test_an_unexpected_failure_becomes_a_red_card(tmp_path, monkeypatch):
    nb, _ = ready_to_convert(tmp_path)
    notebook_convert = importlib.import_module("morphobricks_preprocessing.notebook_convert")

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(notebook_convert, "convert_experiment", broken)
    nb.convert_button.click()
    assert "unexpected problem" in text(nb.convert_status)
    assert "RuntimeError: disk on fire" in text(nb.convert_status)
    assert not nb.convert_button.disabled  # restored for another try


# What next: check the copies, then run on them --------------------------------------------


def test_missing_axes_must_be_confirmed_again_on_the_copies(tmp_path):
    root = tmp_path / "exp"
    swapped_experiment(root, names=("ctrl/r1/a.tif",))
    path = root / "drug" / "r1" / "a.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path, np.zeros((6, 9), np.uint8), ome=True, photometric="minisblack",
        metadata={"axes": "YX"},
    )  # fmt: skip
    nb, _ = checked(root)
    swap_roles(nb)
    nb.missing_as_singleton.button.click()
    confirm_copy_roles(nb)
    nb.convert_folder.value = str(tmp_path / "copies")
    nb.convert_button.click()
    after = text(nb.convert_result)
    assert "press Yes, they have a single plane there again" in after
    assert "--missing-as-singleton" in after
    assert read_conversion_record(tmp_path / "copies")[0].missing_as_singleton


def test_end_to_end_convert_check_the_copies_with_header_axes_and_run(tmp_path):
    nb, root = ready_to_convert(tmp_path)
    output = output_of(root)
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:
            toggle.button.click()
    assert nb.output.value == ""  # the default output folder of the originals
    nb.convert_button.click()
    after = text(nb.convert_result)
    assert "Check the copies in step 1" in after and str(output) in after
    copies = tmp_path / "copies"
    nb.convert_recheck_button.click()
    # Step 1 now checks the copies, with the originals' output folder kept.
    assert nb.folder.value == str(copies) and nb.output.value == str(output)
    assert nb.preflight is not None and nb.preflight.discovery.root == copies
    assert nb.preflight.ok
    assert "".join(nb.mapping.roles) == "ZYX"  # suggested from the copies' named axes
    assert not ui.is_visible(nb.convert_card)  # nothing to convert any more
    nb.labels_confirmed.button.click()
    assert not nb._design_pending()  # the design answered on the originals is kept
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert "Axes checked" in nb.confirmed_status.value
    nb.image_type_checked.button.click()
    result = nb.run()
    assert result.written, result.issues
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["experiment_root"] == copies.as_posix()
    assert {f["axes_from"] for f in manifest["files"]} == {"header"}
    assert manifest["conversion_record_sha256"] == read_conversion_record(copies)[1]
    assert sorted(f["relative_path"] for f in manifest["files"]) == sorted(NAMES)
