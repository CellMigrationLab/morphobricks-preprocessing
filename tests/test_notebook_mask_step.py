"""PP-10d: the notebook's **Describe the masks** button (plan §10, contract §15).

After a run that recorded masks, step 3's button runs the command line's ``masks`` step with
the run's recorded inputs (:mod:`morphobricks_preprocessing.run.mask_step`): it writes
``mask_relationships.json`` holding the lock and shows the mask types, the relationships and
the hierarchy as suggestions to check. It is disabled without such a run, shows refusals with
the existing cards, and is hidden when an embedding brick hides the output folder.
"""

import importlib
import json
import os

import numpy as np
import pytest

from morphobricks_preprocessing import mask_scan
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME
from morphobricks_preprocessing.run.lock import output_lock
from tiff_tree import write_data

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")
ui = importlib.import_module("morphobricks_preprocessing.notebook_style")
PreprocessingNotebook = importlib.import_module(
    "morphobricks_preprocessing.notebook_ui"
).PreprocessingNotebook

IMAGES = ["ctrl/rep1/a.tif", "ctrl/rep2/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]


def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in IMAGES:
        write_data(root / rel, rng.integers(0, 200, (16, 16), dtype=np.uint8), axes="YX")
    return root


def nested_masks(tmp_path):
    """Write cells (two squares) and nuclei (one smaller square inside each cell)."""
    masks = tmp_path / "masks"
    cells = np.zeros((16, 16), np.uint16)
    cells[1:7, 1:7], cells[9:15, 9:15] = 1, 2
    nuclei = np.zeros((16, 16), np.uint16)
    nuclei[3:5, 3:5], nuclei[11:13, 11:13] = 1, 2
    for rel in IMAGES:
        write_data(masks / "cells" / rel, cells)
        write_data(masks / "nuclei" / rel, nuclei)
    return masks


def output_of(root):
    return root.parent / f"{root.name}_MorphoBricks_Preprocessing"


def ran(root, masks=None, *, show_output=True):
    nb = PreprocessingNotebook(str(root))
    nb.welcome()
    nb.folder_step(show_output=show_output)
    nb.axis_step()
    page = nb.run_step()
    if masks is not None:
        nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:
            toggle.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    return nb, page


def test_the_button_is_on_step_3_and_waits_for_a_run_that_recorded_masks(tmp_path):
    root = experiment(tmp_path)
    nb, page = ran(root, nested_masks(tmp_path))
    assert nb.mask_step_card in page.children[1].children
    assert ui.is_visible(nb.mask_step_card)
    assert nb.mask_step_button.disabled
    assert "Available when the output folder holds a run that recorded masks" in (
        nb.mask_step_hint.value
    )
    assert nb._on_describe_masks() is None
    assert nb.run().written
    assert not nb.mask_step_button.disabled
    hint = nb.mask_step_hint.value
    assert "2 mask types" in hint and "cells, nuclei" in hint
    assert f"Describes the run in <b>{output_of(root).resolve()}</b>" in hint
    assert f"of <b>{root.resolve()}</b>" in hint


def test_without_recorded_masks_the_button_stays_disabled(tmp_path):
    root = experiment(tmp_path)
    nb, _ = ran(root)
    assert nb.run().written
    assert nb.mask_step_button.disabled
    assert "it recorded no masks" in nb.mask_step_hint.value
    nb.mask_step_button.click()
    assert nb.mask_step_status.value == ""
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()


def test_describe_the_masks_writes_the_report_and_shows_the_cards(tmp_path, capsys):
    root = experiment(tmp_path)
    nb, _ = ran(root, nested_masks(tmp_path))
    assert nb.run().written
    nb.mask_step_button.click()
    path = output_of(root).resolve() / MASK_REPORT_FILENAME
    assert path.is_file()
    status, shown = nb.mask_step_status.value, nb.mask_step_result.value
    assert "The masks are described: 2 mask types in 4 images." in status
    assert str(path) in status and "Thresholds: the defaults." in status
    assert "suggestions from the masks&#x27; pixels" in status
    assert "<b>Mask types (suggestions to check)</b>" in shown
    assert "one object per label" in shown
    assert "<td>nuclei</td><td>inside</td><td>cells</td>" in shown
    assert "8 of 8" in shown and "0/8/0/0" in shown
    assert "<pre>    cells\n        nuclei</pre>" in shown
    assert nb.mask_step_progress.bar_style == "success"
    assert not nb.mask_step_button.disabled and nb.mask_step_button.icon == "sitemap"
    # The same report as the command line's masks step, with the recorded inputs.
    written = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    assert main(["masks", "--output", str(output_of(root))]) == 0
    again = json.loads(path.read_text(encoding="utf-8"))
    assert {k: v for k, v in again.items() if k != "versions"} == {
        k: v for k, v in written.items() if k != "versions"
    }


def test_describe_the_masks_reuses_the_ids_of_your_masks_and_asks_no_name(tmp_path):
    # Stage 1 PR 16: masks are named once, on Your masks; step 3 shows those IDs.
    root = experiment(tmp_path)
    nb, _ = ran(root, nested_masks(tmp_path))
    question = nb.mask_card.questions["nuclei"]
    question.id_box.value = "Nuclei_seg"
    question.choice.value = "instances"
    assert nb.run().written
    nb.mask_step_button.click()
    status, shown = nb.mask_step_status.value, nb.mask_step_result.value
    assert "name each mask" not in status
    assert "keeps the ID it was given on Your masks" in status
    assert "<th>Mask</th><th>Declared kind</th><th>Kind (suggested)</th>" in shown
    assert "<td>Nuclei_seg (nuclei)</td><td>objects</td><td>one object per label</td>" in shown
    assert "<td>cells</td><td>not described yet</td><td>" in shown


def test_a_child_equally_in_two_parents_is_shown_with_no_parent(tmp_path):
    # Plan §12 question 74: a tie gives no parent, and the card names the objects.
    root = experiment(tmp_path)
    masks = nested_masks(tmp_path)
    cells = np.zeros((16, 16), np.uint16)
    cells[0:8, 0:8], cells[0:8, 8:16] = 1, 2
    nuclei = np.zeros((16, 16), np.uint16)
    nuclei[3:5, 7:9] = 1  # two pixels in each cell
    for rel in IMAGES:
        write_data(masks / "cells" / rel, cells)
        write_data(masks / "nuclei" / rel, nuclei)
    nb, _ = ran(root, masks)
    assert nb.run().written
    nb.mask_step_button.click()
    shown = nb.mask_step_result.value
    assert "<b>Objects given no parent (a tie, to check)</b>" in shown
    assert "<li>nuclei in cells: 4 objects of 4 equally in two or more cells</li>" in shown


def test_a_locked_output_folder_shows_the_lock_card_and_writes_nothing(tmp_path):
    root = experiment(tmp_path)
    nb, _ = ran(root, nested_masks(tmp_path))
    assert nb.run().written
    with output_lock(output_of(root)):
        assert nb._on_describe_masks() is None
        assert ui.is_visible(nb.mask_step_lock.card)
    assert "the output folder is locked" in nb.mask_step_status.value
    assert nb.mask_step_result.value == ""
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()
    assert "Describe the masks again" in nb.mask_step_lock.again
    assert nb._on_describe_masks() is not None
    assert not ui.is_visible(nb.mask_step_lock.card)


def test_mask_errors_since_the_run_are_shown_and_nothing_is_read(tmp_path):
    root = experiment(tmp_path)
    masks = nested_masks(tmp_path)
    nb, _ = ran(root, masks)
    assert nb.run().written
    write_data(masks / "cells" / IMAGES[0], np.zeros((8, 8), np.uint16))  # wrong size now
    assert nb._on_describe_masks() is None
    assert "An error stops the step: no mask was read." in nb.mask_step_status.value
    assert "mask size mismatch" in nb.mask_step_result.value
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()


def test_masks_whose_labels_cannot_be_used_get_a_red_card(tmp_path):
    root = experiment(tmp_path)
    masks = nested_masks(tmp_path)
    nb, _ = ran(root, masks)
    assert nb.run().written
    write_data(masks / "cells" / IMAGES[0], np.full((16, 16), 2**31, np.uint32))
    assert nb._on_describe_masks() is None
    assert "The masks cannot be used." in nb.mask_step_status.value
    assert "Nothing was written." in nb.mask_step_status.value
    assert nb.mask_step_progress.bar_style == "danger"


def test_a_mask_changed_while_it_is_described_says_to_wait_not_to_correct_it(tmp_path, monkeypatch):
    # Issue #165 (review of #180): a mask still being written is not a mask to correct.
    root = experiment(tmp_path)
    masks = nested_masks(tmp_path)
    nb, _ = ran(root, masks)
    assert nb.run().written
    read, changed = mask_scan.read_mask, masks / "cells" / IMAGES[0]

    def read_then_rewrite(mask, *args, **kwargs):
        pixels = read(mask, *args, **kwargs)
        stat = changed.stat()
        write_data(changed, np.zeros((16, 16), np.uint16))
        os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        return pixels

    monkeypatch.setattr(mask_scan, "read_mask", read_then_rewrite)
    assert nb._on_describe_masks() is None
    status = nb.mask_step_status.value
    assert "changed while they were read" in status and "Nothing was written." in status
    assert "Correct or replace" not in status
    assert "mask changed during scan" in nb.mask_step_result.value
    assert "Wait until nothing is writing to these masks" in nb.mask_step_result.value
    assert nb.mask_step_progress.bar_style == "danger"
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()


def test_a_new_run_clears_the_description(tmp_path):
    root = experiment(tmp_path)
    nb, _ = ran(root, nested_masks(tmp_path))
    assert nb.run().written
    nb.mask_step_button.click()
    assert nb.mask_step_result.value
    assert nb.run().written
    assert nb.mask_step_result.value == "" and nb.mask_step_status.value == ""
    assert not nb.mask_step_button.disabled


def test_an_embedding_brick_that_hides_the_output_folder_gets_no_card(tmp_path):
    root = experiment(tmp_path)
    nb, _ = ran(root, show_output=False)
    assert not ui.is_visible(nb.mask_step_card)
    nb.run()
    assert not ui.is_visible(nb.mask_step_card)


def reopened(root, *, show_output=True):
    """Open a new notebook on an experiment, as after a restart: step 1's folders, no run."""
    nb = PreprocessingNotebook(str(root))
    nb.welcome()
    nb.folder_step(show_output=show_output)
    nb.axis_step()
    nb.run_step()
    return nb


def test_a_reopened_notebook_describes_the_earlier_run(tmp_path):
    root = experiment(tmp_path)
    first, _ = ran(root, nested_masks(tmp_path))
    assert first.run().written
    nb = reopened(root)
    assert nb.result is None
    assert not nb.mask_step_button.disabled
    hint = nb.mask_step_hint.value
    assert f"Describes the run in <b>{output_of(root).resolve()}</b>, written " in hint
    assert "cells, nuclei" in hint and str((tmp_path / "masks").resolve()) in hint
    result = nb._on_describe_masks()
    assert result is not None and result.path == output_of(root).resolve() / MASK_REPORT_FILENAME
    written = json.loads(result.path.read_text(encoding="utf-8"))
    result.path.unlink()
    assert main(["masks", "--output", str(output_of(root))]) == 0
    again = json.loads(result.path.read_text(encoding="utf-8"))
    assert {k: v for k, v in again.items() if k != "versions"} == {
        k: v for k, v in written.items() if k != "versions"
    }


def test_a_reopened_notebook_follows_the_output_folder_of_step_1(tmp_path):
    root = experiment(tmp_path)
    first, _ = ran(root, nested_masks(tmp_path))
    assert first.run().written
    nb = reopened(root)
    assert not nb.mask_step_button.disabled
    nb.output.value = str(tmp_path / "elsewhere")
    assert nb.mask_step_button.disabled
    assert "Available when the output folder holds" in nb.mask_step_hint.value
    nb.output.value = str(output_of(root))
    assert not nb.mask_step_button.disabled


def test_a_reopened_notebook_refuses_when_the_experiment_folder_moved(tmp_path):
    root = experiment(tmp_path)
    first, _ = ran(root, nested_masks(tmp_path))
    assert first.run().written
    output = output_of(root)
    root.rename(tmp_path / "moved")
    nb = PreprocessingNotebook(str(tmp_path / "moved"), str(output))
    nb.welcome()
    nb.folder_step()
    nb.axis_step()
    nb.run_step()
    assert not nb.mask_step_button.disabled
    assert nb._on_describe_masks() is None
    assert "An error stops the step: no mask was read." in nb.mask_step_status.value
    assert "does not exist or is not a folder" in nb.mask_step_result.value
    assert not (output / MASK_REPORT_FILENAME).exists()


def test_a_reopened_notebook_refuses_when_the_masks_folder_moved(tmp_path):
    root = experiment(tmp_path)
    masks = nested_masks(tmp_path)
    first, _ = ran(root, masks)
    assert first.run().written
    masks.rename(tmp_path / "moved_masks")
    nb = reopened(root)
    assert nb._on_describe_masks() is None
    assert "The masks folder" in nb.mask_step_result.value
    assert "does not exist or is not a folder" in nb.mask_step_result.value
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()


def test_a_reopened_notebook_without_a_run_keeps_the_button_disabled(tmp_path):
    root = experiment(tmp_path)
    nb = reopened(root)
    assert nb.mask_step_button.disabled
    assert nb._on_describe_masks() is None
    assert nb.mask_step_status.value == ""


def test_an_embedding_brick_never_gets_the_earlier_run_described(tmp_path):
    root = experiment(tmp_path)
    first, _ = ran(root, nested_masks(tmp_path))
    assert first.run().written
    nb = reopened(root, show_output=False)
    assert not ui.is_visible(nb.mask_step_card)
    assert nb.mask_step_button.disabled
    assert nb._on_describe_masks() is None
    assert not (output_of(root) / MASK_REPORT_FILENAME).exists()
