"""PP-10c: the notebook's masks folder and mask-axis statements (design v0.4 §2, §3).

Step 1 takes the masks folder ``check --masks`` takes and pairs the masks with the same
functions; the Masks card shows the mask types, the images without masks and the masks'
messages; mask types whose axes must be stated get a choice (as ``--mask-axes``), offered
only where needed and applied only on **Check the masks**; the run is given the same masks
folder and mask axes; a recorded masks folder is reused unless dropped explicitly.
"""

import importlib

import numpy as np
import pytest

from morphobricks_preprocessing import preflight, read_manifest
from tiff_tree import write_data, write_plain

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
ui = importlib.import_module("morphobricks_preprocessing.notebook_style")
PreprocessingNotebook = importlib.import_module(
    "morphobricks_preprocessing.notebook_ui"
).PreprocessingNotebook

IMAGES = ["ctrl/rep1/a.tif", "ctrl/rep2/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]


def labels(shape):
    return (np.arange(np.prod(shape)).reshape(shape) % 5 + 1).astype(np.uint16)


def experiment(tmp_path, shape=(16, 16), axes="YX"):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in IMAGES:
        write_data(root / rel, rng.integers(0, 200, shape, dtype=np.uint8), axes=axes)
    return root


def output_of(root):
    return root.parent / f"{root.name}_MorphoBricks_Preprocessing"


def notebook(root, masks=None, **kwargs):
    nb = PreprocessingNotebook(str(root), **kwargs)
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    if masks is not None:
        nb.masks_folder.value = str(masks)
    nb.check_button.click()
    return nb


def confirm_everything(nb):
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:  # a later session loads the confirmed design
            toggle.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()


def still_to_do(nb):
    if not nb.run_button.disabled:
        return ""
    return nb.readiness.value.split("Still to do: ")[1].split(".")[0]


# 1. The masks folder field and the pairing --------------------------------------------


def test_the_masks_field_pairs_the_masks_as_check_masks_does(tmp_path):
    root = experiment(tmp_path)
    masks = tmp_path / "masks"
    for rel in IMAGES:  # one subfolder per mask type, mirroring the experiment
        write_data(masks / "cells" / rel, labels((16, 16)))
    for rel in IMAGES[:3]:  # or the type after the image name; one image has none
        write_data(masks / rel.replace("a.tif", "a_nuclei.tif"), labels((16, 16)))
    nb = notebook(root, masks)
    assert nb.preflight.masks == preflight(root, masks_folder=masks).masks
    assert ui.is_visible(nb.masks_input_card) and ui.is_visible(nb.masks_card)
    assert "2 mask types for 4 images" in nb.masks_status.value
    table = nb.masks_table.value
    assert "cells" in table and "nuclei" in table and "Images without one" in table
    assert "Without a <b>nuclei</b> mask" in table and "drug/rep2/a.tif" in table
    assert "mask missing" in nb.masks_issues.value
    assert nb.mask_axes_box.children == ()  # every mask names its axes: nothing asked
    confirm_everything(nb)
    assert "Masks: <b>2 mask types</b> (cells, nuclei)" in nb.plan.value
    result = nb.run()
    assert result.written
    recorded = read_manifest(result.manifest_path).masks
    assert recorded["folder"] == masks.resolve().as_posix()
    assert sorted(recorded["types"]) == ["cells", "nuclei"]


def test_browse_fills_the_masks_folder_or_says_how_to_paste_it(tmp_path, monkeypatch):
    root = experiment(tmp_path)
    nb = notebook(root)
    monkeypatch.setattr(ui, "ask_directory", lambda initial: str(tmp_path / "masks"))
    nb.masks_browse.click()
    assert nb.masks_folder.value == str(tmp_path / "masks")
    monkeypatch.setattr(ui, "ask_directory", lambda initial: None)  # Google Colab
    nb.masks_browse.click()
    assert "<b>Copy path</b>" in nb.folder_status.value


def test_mask_errors_are_shown_on_the_card_and_block_the_run(tmp_path):
    root = experiment(tmp_path)
    nb = notebook(root, root / "ctrl")  # inside the experiment
    assert "masks inside experiment" in nb.masks_issues.value
    assert "some masks cannot be used" in nb.masks_status.value
    assert "Action required" in nb.folder_status.value
    confirm_everything(nb)
    assert still_to_do(nb) == "fix any mask errors (step 1, Masks)"
    assert nb.run() is None


def test_a_new_masks_folder_is_paired_on_check_the_masks_keeping_the_answers(tmp_path):
    root = experiment(tmp_path)
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_data(masks / "cells" / rel, labels((16, 16)))
    nb = notebook(root)
    assert not ui.is_visible(nb.masks_card)  # no masks folder: nothing to show
    confirm_everything(nb)
    assert not nb.run_button.disabled
    nb.masks_folder.value = str(masks)
    assert still_to_do(nb).startswith("check the masks again")  # never used unpaired
    nb.masks_button.click()
    assert nb.labels_confirmed.value and nb.confirmed_mapping is not None
    assert "1 mask type for 4 images" in nb.masks_status.value
    assert not nb.run_button.disabled
    assert read_manifest(nb.run().manifest_path).masks["types"] == ["cells"]


# 2. Mask-axis statements per mask type -------------------------------------------------


def test_plain_stack_masks_get_their_axes_stated_never_guessed(tmp_path):
    root = experiment(tmp_path, (3, 16, 16), "TYX")
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_plain(masks / "Cells" / rel, labels((3, 16, 16)))  # IYX: axes not named
    nb = notebook(root, masks)
    assert "mask not 2d" in nb.masks_issues.value
    choice = nb._mask_choices["Cells"]
    assert choice.value == ""  # nothing chosen for the user
    offered = [value for _, value in choice.options]
    assert offered[:3] == ["", "TYX", "ZYX"] and "YX" not in offered
    assert "<b>Cells</b> (axes in the files: IYX)" in nb.mask_axes_box.children[1].value
    confirm_everything(nb)
    assert still_to_do(nb) == "fix any mask errors (step 1, Masks)"
    choice.value = "TYX"  # chosen, not yet applied
    assert nb.preflight.masks.files[0].axes == ""
    assert still_to_do(nb).startswith("check the masks again")
    assert "Click Check the masks" in nb.masks_status.value
    nb.masks_button.click()
    assert nb.preflight.masks.ok
    assert {f.axes_from for f in nb.preflight.masks.files} == {"stated"}
    assert "TYX (stated)" in nb.masks_table.value
    result = nb.run()
    assert result.written
    files = read_manifest(result.manifest_path).masks["files"]
    assert {(f["roles"], f["axes_from"]) for f in files} == {("TYX", "stated")}


def test_a_2d_mask_of_a_stack_is_shared_only_when_stated(tmp_path):
    root = experiment(tmp_path, (2, 16, 16), "ZYX")
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_data(masks / "Cells" / rel, labels((16, 16)), axes="YX")
    nb = notebook(root, masks)
    assert nb.mask_axes_box.children == ()  # the planes are known once the roles are
    confirm_everything(nb)
    assert "mask planes mismatch" in nb.masks_issues.value
    choice = nb._mask_choices["Cells"]
    assert [value for _, value in choice.options] == ["", "YX"]
    assert "shared by every plane" in choice.options[1][0]
    assert still_to_do(nb) == "fix any mask errors (step 1, Masks)"
    choice.value = "YX"
    nb.masks_button.click()
    assert nb.preflight.masks.ok and not nb.run_button.disabled
    result = nb.run()
    assert result.written
    files = read_manifest(result.manifest_path).masks["files"]
    assert {(f["roles"], f["axes_from"]) for f in files} == {("YX", "stated")}


# 3. Recorded masks: reused, named when changed, dropped only explicitly ----------------


def test_a_recorded_masks_folder_is_reused_or_dropped_explicitly(tmp_path):
    root = experiment(tmp_path)
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_data(masks / "cells" / rel, labels((16, 16)))
    first = notebook(root, masks)
    confirm_everything(first)
    assert first.run().written
    again = notebook(root)  # a new session: the box is empty
    assert "used a masks folder" in again.masks_recorded.value
    assert again.preflight.masks.folder == masks.resolve()  # what the run would use
    assert ui.is_visible(again.masks_drop)
    confirm_everything(again)
    assert "from the masks folder of the earlier run" in again.plan.value
    assert again._planned_changes(output_of(root).resolve()) == ()
    again.masks_drop.click()
    assert "dropped" in again.masks_recorded.value
    assert still_to_do(again).startswith("check the masks again")
    again.masks_button.click()
    assert again.preflight.masks is None and not again.run_button.disabled
    assert "<b>masks_folder</b>" in again.plan.value and "none (dropped)" in again.plan.value
    result = again.run()
    assert result.written and read_manifest(result.manifest_path).masks is None
    # Nothing is recorded any more: running again names no change, and offers no drop.
    assert not ui.is_visible(again.masks_drop) and "<b>masks_folder</b>" not in again.plan.value
    assert again.run().input_changes == ()


# 5. Embedding ---------------------------------------------------------------------------


def test_an_embedded_step_has_no_masks_field_and_pairs_no_recorded_masks(tmp_path):
    root = experiment(tmp_path)
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_data(masks / "cells" / rel, labels((16, 16)))
    first = notebook(root, masks)
    confirm_everything(first)
    assert first.run().written  # the default output folder now records the masks
    nb = PreprocessingNotebook(str(root))
    page = nb.folder_step(show_output=False, show_next=False)
    nb.axis_step(show_next=False)
    nb.check_button.click()
    parts = page.children[1].children
    assert nb.masks_input_card not in parts and nb.masks_card not in parts
    assert nb.preflight.masks is None and nb.preflight.ok
